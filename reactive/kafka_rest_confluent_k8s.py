import os
import yaml
import hashlib
import datetime
from charms.reactive import (
    set_flag,
    clear_flag,
    when,
    when_not,
    when_not_all,
    endpoint_from_flag,
    data_changed,
)
from charmhelpers.core.hookenv import (
    status_set,
    open_port,
    config,
    log,
    charm_dir,
)
from charmhelpers.core.templating import render


conf = config()


####################################################
#    Install (one time)
#    Generated files (k8s manifest, nginx config)
#    are stored in /etc/kafka-rest
####################################################


@when_not('kafka-rest-confluent-k8s.installed')
def setup_kafka_rest():
    try:
        os.makedirs('/etc/kafka-rest')
    except OSError as e:
        log(e)
        status_set('blocked', 'Could not create setup dir.')
        return
    set_flag('kafka-rest-confluent-k8s.installed')


####################################################
#    Relation states / messages
####################################################


@when_not('endpoint.kubernetes.available')
def wait_k8s_relations():    
    status_set('blocked', 'Waiting on k8s deployer relation')


@when_not('kafka.ready')
def wait_kafka_relations():    
    status_set('blocked', 'Waiting on kafka relation')


@when_not('config.set.pods')
def wait_pods_config():
    status_set('blocked', 'Waiting for pods config')


@when('config.changed.pods')
def pods_config_changed():
    reset_k8s_flags()


@when_not_all('endpoint.kubernetes.available',
              'kafka.ready',
              'config.set.pods')
def remove_request_state():
    reset_k8s_flags()


####################################################
#    Kubernetes deployer
####################################################


@when('endpoint.kubernetes.available',
      'config.set.pods',
      'kafka.ready')
@when_not('kafka-rest.requested')
def request_rest_setup():
    """
    Request the creation of kafka-rest resources via the 
    kubernetes deployer.
    """
    status_set('active', 'Creating resource request')
    kafka = endpoint_from_flag('kafka.ready')
    k8s = endpoint_from_flag('endpoint.kubernetes.available')
    k8s_uuid = k8s.get_uuid()
    juju_app_name = os.environ['JUJU_UNIT_NAME'].split('/')[0]

    kafka_brokers = []
    for broker in kafka.kafkas():
        kafka_brokers.append("{}:{}".format(broker['host'], broker['port']))
    zookeepers = []
    for zoo in kafka.zookeepers():
        zookeepers.append("{}:{}".format(zoo['host'], zoo['port']))
    # Remove duplicate Zookeeper entries since every Kafka broker unit
    # sends all Zookeeper info.
    zookeepers = list(set(zookeepers))

    properties = {
        'KAFKA_REST_ZOOKEEPER_CONNECT': ','.join(zookeepers),
        'KAFKA_REST_BOOTSTRAP_SERVERS': ','.join(kafka_brokers),
        'KAFKA_REST_LISTENERS': "http://0.0.0.0:8082",
        'KAFKA_REST_HOST_NAME': 'localhost',
    }    

    resource_context = {
        'configmap_name': 'cfgmap-{}'.format(k8s_uuid),
        'label': k8s_uuid,
        'properties': properties,
        'service_name': 'svc-{}'.format(k8s_uuid),
        'port': 8082,
        'deployment_name': 'depl-{}'.format(k8s_uuid),
        'replicas': conf.get('pods'),
        'configmap_annotation': hashlib.sha1(datetime.datetime.now()
                                             .isoformat()
                                             .encode('utf-8')).hexdigest(),
        'container_name': 'kafka-rest-confluent',
        'image': 'confluentinc/cp-kafka-rest:3.1.0',
    }

    render(source='resources.j2',
           target='/etc/kafka-rest/{}.yaml'.format(juju_app_name),
           context=resource_context)

    resources = []
    with open('/etc/kafka-rest/{}.yaml'.format(juju_app_name), 'r') as f:
        docs = yaml.load_all(f)
        for doc in docs:
            resources.append(doc)
    k8s.send_create_request(resources)
    set_flag('kafka-rest.requested')


@when('kafka-rest.requested',
      'endpoint.kubernetes.new-status')
def kubernetes_status():
    status_set('waiting', 'Waiting on k8s deployment')
    k8s = endpoint_from_flag('endpoint.kubernetes.new-status')
    k8s_status = k8s.get_status()
    if not k8s_status or 'status' not in k8s_status:
        return    
    # Check if pods are ready
    replicas = conf.get('pods')
    for resource in k8s_status['status']:
        if resource['kind'] == "Deployment":
            if ('readyReplicas' not in resource['status'] 
                or resource['status']['readyReplicas'] != replicas):
                return
    status_set('active', 'ready')
    clear_flag('endpoint.kubernetes.new-status')
    set_flag('kafka-rest.running')


def reset_k8s_flags():
    """
    This method will clear all flags so a new request will be made to
    the deployer if available.
    """
    clear_flag('kafka-rest.requested')
    clear_flag('kafka-rest.running')


####################################################
#    Upstream
####################################################


@when('endpoint.upstream.available',
      'endpoint.kubernetes.available',
      'kafka-rest.running')
@when_not('upstream.configured')
def configure_upstream():
    status_set('maintenance', 'Configuring upstream')
    reverse_proxy = endpoint_from_flag('endpoint.upstream.available')
    k8s = endpoint_from_flag('endpoint.kubernetes.available')

    k8s_workers = k8s.get_worker_ips()
    k8s_status = k8s.get_status()

    if not k8s_workers or not k8s_status:
        status_set('blocked', 'Failed to configure upstream')
        return
    # Find the service Nodeport
    nodeport = None
    for resource in k8s_status['status']:
        if resource['kind'] == "Service":
            nodeport = resource['spec']['ports'][0]['nodePort']
    # Setup the upstream service endpoints
    services = []
    for worker_ip in k8s_workers:
        services.append({
            'hostname': worker_ip,
            'port': nodeport,
        })
    # Create an upstream name
    upstream = os.environ['JUJU_UNIT_NAME'].split('/')[0]
    # Create the nginx config files in /etc/kafka-rest
    render(source='upstream.j2',
           target='/etc/kafka-rest/upstream.nginx',
           context={
               'upstream': upstream,
               'services': services,
           })
    render(source='location.j2',
           target='/etc/kafka-rest/location.nginx',
           context={
               'upstream': upstream,
           })
    # Send the config upstream
    with open('/etc/kafka-rest/location.nginx') as f:
        reverse_proxy.publish_location(f.read())
    with open('/etc/kafka-rest/upstream.nginx') as f:
        reverse_proxy.publish_config(f.read())
    status_set('active', 'ready')
    set_flag('upstream.configured')


@when('upstream.configured')
@when_not_all('endpoint.upstream.available',
              'endpoint.kubernetes.available')
def reset_upstream():
    clear_flag('upstream.configured')
