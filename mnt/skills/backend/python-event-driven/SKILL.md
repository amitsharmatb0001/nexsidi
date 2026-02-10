---
name: Python Event-Driven (FastStream)
description: Asynchronous Publisher/Consumer architecture using FastStream and RabbitMQ/PubSub for resilient background processing.
tags: [python, faststream, rabbitmq, event-driven, backend]
---

# Python Event-Driven (FastStream) Template

## 1. Architectural Overview
Decouples API (Producer) from Side Effects (Consumer) using Message Brokers.
- **Framework**: FastStream (Unified API for RabbitMQ/Kafka/PubSub)
- **Validation**: Pydantic (Shared Schemas)
- **Resilience**: Retries + Dead Letter Queues (DLQ)

## 2. Component Structure

### Publisher (FastAPI + RabbitRouter)
"Fire-and-Forget" pattern in the API.

```python
# main.py
from faststream.rabbit.fastapi import RabbitRouter
router = RabbitRouter("amqp://guest:guest@localhost:5672/")

@router.post("/orders/")
async def create_order(order: OrderCreated):
    # 1. DB Persist
    # 2. Publish Event
    await router.broker.publish(
        message=order,
        exchange="orders",
        routing_key="order.created.v1"
    )
    return {"status": "Accepted"}
```

### Consumer (Worker)
Independent worker process.

```python
# worker.py
from faststream import FastStream
from faststream.rabbit import RabbitBroker

broker = RabbitBroker("amqp://guest:guest@localhost:5672/")

@broker.subscriber(queue="inventory_updates", exchange="orders", routing_key="order.created.*")
async def handle_order(msg: OrderCreated):
    await update_inventory(msg.product_ids)
```

## 3. Resilience Strategy

### 5 Retries then DLQ
Use Middleware or Broker Config.
- **RabbitMQ**: Application-level middleware for retries + `x-dead-letter-exchange` for DLQ.
- **GCP Pub/Sub**: Native Retry Policy + Dead Letter Topic.

**Retry Middleware Example:**
```python
async def consume_scope(self, call_next, msg):
    retries = 0
    while True:
        try:
            return await call_next(msg)
        except Exception:
            if retries >= 5: raise # Trigger DLQ
            await asyncio.sleep(backoff)
            retries += 1
```

## 4. Execution
Run workers separately from the API for independent scaling.
```bash
faststream run worker:app --workers 2
```
