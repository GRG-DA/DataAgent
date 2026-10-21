"""DataAgent billing server.

A small FastAPI service that owns the Razorpay keys and license signing key:
creates orders, verifies payments, receives webhooks, mints Ed25519 product
keys, emails them, and serves a signed entitlement read API for the desktop
app. Run with: uvicorn billing_server.main:app --port 8090
"""
