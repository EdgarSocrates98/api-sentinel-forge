"""Lab service — FastAPI app exposing /orders endpoints."""

from fastapi import FastAPI

app = FastAPI(title="orders-api")


@app.get("/orders")
def list_orders():
    return []


@app.post("/orders")
def create_order(order: dict):
    return order


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    return {"id": order_id}
