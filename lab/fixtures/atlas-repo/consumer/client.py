"""Lab consumer — literal HTTP calls into orders-api."""
import requests


def fetch_orders():
    return requests.get("http://orders.local/orders")


def fetch_one():
    return requests.get("http://orders.local/orders/abc-123")


def post_relative():
    return requests.post("/orders")
