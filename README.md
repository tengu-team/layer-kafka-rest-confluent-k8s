# Kafka-REST-Confluent-k8s

Provides a RESTful interface to a Kafka cluster by [Confluent](https://www.confluent.io/) running inside a Kubernetes cluster.

By default version 3.1 of the REST API is used to be compliant with the [Kafka Bigtop charm](https://jujucharms.com/kafka/). Full API overview can be found [here](https://docs.confluent.io/3.1.0/kafka-rest/docs/api.html).

## Usage

```
juju deploy ./kafka-rest-confluent-k8s kafka-rest
juju add-relation kafka-rest kafka
juju add-relation kafka-rest kubernetes-deployer

# Reverse proxy
juju add-relation kafka-rest nginx-api-gateway

# Add / remove rest instances
juju config kafka-rest "pods=3"
```

## Caveats

- This charm should only be used for producing data to Kafka. Creating consumers will return a wrong baseline url and require sticky sessions if #pods > 1.

## Authors

This software was created in the [IBCN research group](https://www.ibcn.intec.ugent.be/) of [Ghent University](https://www.ugent.be/en) in Belgium. This software is used in [Tengu](https://tengu.io), a project that aims to make experimenting with data frameworks and tools as easy as possible.

 - Sander Borny <sander.borny@ugent.be>