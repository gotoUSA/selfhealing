"""
Webhook Sender Service for Stage 20: Delayed/Out-of-Order Webhook Handling Test

This Flask-based service simulates an external Payment Gateway (PG) webhook sender.
It is responsible for:
  - Delayed webhook delivery (webhook arrives after order timeout)
  - Out-of-order webhook delivery (CANCEL before CONFIRM)
  - Duplicate webhook delivery (same webhook sent multiple times)

Architecture:
  - Receives simulation requests from Locust
  - Asynchronously sends webhooks to the web service
  - Tracks webhook delivery status for verification

This service runs in a separate Docker container to model real system boundaries.
"""

import os
import time
import json
import hmac
import hashlib
import threading
import logging
from datetime import datetime
from collections import defaultdict
from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify
import requests

# =============================================================================
# Configuration
# =============================================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Target web service URL
WEB_SERVICE_URL = os.environ.get("WEB_SERVICE_URL", "http://web:8000")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "test_webhook_secret_key_12345")
TOSS_WEBHOOK_ENDPOINT = "/api/webhooks/toss/"
PAYMENT_CONFIRM_ENDPOINT = "/api/payments/confirm/"
PAYMENT_CANCEL_ENDPOINT = "/api/payments/cancel/"

# Worker pool for async webhook delivery
MAX_WORKERS = 10
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# =============================================================================
# Data Models
# =============================================================================

class WebhookType(Enum):
    NORMAL = "NORMAL"
    DELAYED = "DELAYED"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DUPLICATE = "DUPLICATE"


@dataclass
class WebhookDelivery:
    """Track webhook delivery attempt"""
    id: str
    order_id: str
    payment_key: str
    webhook_type: str
    scheduled_at: float
    delivered_at: Optional[float] = None
    response_code: Optional[int] = None
    success: bool = False
    error_message: Optional[str] = None
    attempt_count: int = 0


# =============================================================================
# Statistics Tracking
# =============================================================================

_stats = {
    "service_start_time": time.time(),
    # Request counts
    "requests_received": 0,
    "normal_webhooks_scheduled": 0,
    "delayed_webhooks_scheduled": 0,
    "out_of_order_webhooks_scheduled": 0,
    "duplicate_webhooks_scheduled": 0,
    # Delivery counts
    "webhooks_delivered": 0,
    "webhooks_failed": 0,
    "delivery_latencies_ms": [],
    # By type
    "normal_delivered": 0,
    "delayed_delivered": 0,
    "out_of_order_delivered": 0,
    "duplicate_delivered": 0,
    # Response tracking
    "response_200": 0,
    "response_400": 0,
    "response_401": 0,
    "response_409": 0,
    "response_500": 0,
    "response_other": 0,
}

_stats_lock = threading.Lock()

# Track all deliveries for verification
_deliveries: Dict[str, List[WebhookDelivery]] = defaultdict(list)
_deliveries_lock = threading.Lock()


def record_stat(key: str, increment: int = 1):
    """Thread-safe stat recording"""
    with _stats_lock:
        _stats[key] += increment


def record_delivery(delivery: WebhookDelivery):
    """Record delivery for verification"""
    with _deliveries_lock:
        _deliveries[delivery.order_id].append(delivery)


def record_latency(latency_ms: float):
    """Record delivery latency"""
    with _stats_lock:
        _stats["delivery_latencies_ms"].append(latency_ms)


# =============================================================================
# Webhook Signature Generation
# =============================================================================

def generate_webhook_signature(payload: dict) -> str:
    """
    Generate HMAC signature for webhook payload.
    Mimics Toss Payments webhook signature generation.
    """
    payload_str = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
    signature = hmac.new(
        WEBHOOK_SECRET.encode(),
        payload_str.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature


# =============================================================================
# Webhook Payload Generation
# =============================================================================

def create_payment_done_payload(order_id: str, payment_key: str, amount: int = 10000) -> dict:
    """Create PAYMENT.DONE event payload (Toss format)"""
    return {
        "eventType": "PAYMENT.DONE",
        "createdAt": datetime.utcnow().isoformat() + "Z",
        "data": {
            "paymentKey": payment_key,
            "orderId": str(order_id),
            "status": "DONE",
            "approvedAt": datetime.utcnow().isoformat() + "Z",
            "totalAmount": amount,
            "method": "카드",
            "transactionKey": f"txn_{payment_key}",
        }
    }


def create_payment_canceled_payload(order_id: str, payment_key: str, reason: str = "User requested") -> dict:
    """Create PAYMENT.CANCELED event payload (Toss format)"""
    return {
        "eventType": "PAYMENT.CANCELED",
        "createdAt": datetime.utcnow().isoformat() + "Z",
        "data": {
            "paymentKey": payment_key,
            "orderId": str(order_id),
            "status": "CANCELED",
            "canceledAt": datetime.utcnow().isoformat() + "Z",
            "cancelAmount": 10000,
            "cancelReason": reason,
            "transactionKey": f"txn_{payment_key}_cancel",
        }
    }


def create_payment_failed_payload(order_id: str, payment_key: str, reason: str = "Payment declined") -> dict:
    """Create PAYMENT.FAILED event payload (Toss format)"""
    return {
        "eventType": "PAYMENT.FAILED",
        "createdAt": datetime.utcnow().isoformat() + "Z",
        "data": {
            "paymentKey": payment_key,
            "orderId": str(order_id),
            "status": "FAILED",
            "failedAt": datetime.utcnow().isoformat() + "Z",
            "failureCode": "PAYMENT_DECLINED",
            "failureMessage": reason,
        }
    }


# =============================================================================
# Webhook Delivery Functions
# =============================================================================

def send_webhook_to_web(payload: dict, delivery: WebhookDelivery) -> bool:
    """
    Send webhook to the web service.
    Returns True if successful (2xx response).
    """
    delivery.attempt_count += 1
    start_time = time.time()
    
    try:
        signature = generate_webhook_signature(payload)
        headers = {
            "Content-Type": "application/json",
            "X-Toss-Webhook-Signature": signature,
        }
        
        url = f"{WEB_SERVICE_URL}{TOSS_WEBHOOK_ENDPOINT}"
        
        logger.info(f"[DEBUG] Sending webhook to {url}")
        logger.info(f"[DEBUG] Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=10
        )
        
        delivery.delivered_at = time.time()
        delivery.response_code = response.status_code
        
        latency_ms = (delivery.delivered_at - start_time) * 1000
        record_latency(latency_ms)
        
        # Track response codes
        if response.status_code == 200:
            record_stat("response_200")
            delivery.success = True
        elif response.status_code == 400:
            record_stat("response_400")
        elif response.status_code == 401:
            record_stat("response_401")
        elif response.status_code == 409:
            record_stat("response_409")
        elif response.status_code >= 500:
            record_stat("response_500")
        else:
            record_stat("response_other")
            
        logger.info(f"[DEBUG] Webhook response: {response.status_code} - {response.text[:200]}")
        
        if response.status_code in [200, 201]:
            record_stat("webhooks_delivered")
            return True
        else:
            record_stat("webhooks_failed")
            delivery.error_message = f"HTTP {response.status_code}: {response.text[:100]}"
            return False
            
    except requests.exceptions.Timeout:
        delivery.error_message = "Request timeout"
        record_stat("webhooks_failed")
        logger.error(f"[DEBUG] Webhook timeout for order {delivery.order_id}")
        return False
    except Exception as e:
        delivery.error_message = str(e)
        record_stat("webhooks_failed")
        logger.error(f"[DEBUG] Webhook error: {e}")
        return False
    finally:
        record_delivery(delivery)


def send_confirm_to_web(order_id: str, payment_key: str, amount: int, auth_header: str = None) -> bool:
    """
    Send payment confirm request to web service.
    Used for direct confirm/cancel testing (not via webhook).
    """
    try:
        url = f"{WEB_SERVICE_URL}{PAYMENT_CONFIRM_ENDPOINT}"
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header
            
        response = requests.post(
            url,
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": amount,
            },
            headers=headers,
            timeout=10
        )
        
        logger.info(f"[DEBUG] Confirm response: {response.status_code}")
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.error(f"[DEBUG] Confirm error: {e}")
        return False


def send_cancel_to_web(payment_key: str, reason: str, auth_header: str = None) -> bool:
    """Send payment cancel request to web service"""
    try:
        url = f"{WEB_SERVICE_URL}{PAYMENT_CANCEL_ENDPOINT}"
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header
            
        response = requests.post(
            url,
            json={
                "payment_key": payment_key,
                "cancel_reason": reason,
            },
            headers=headers,
            timeout=10
        )
        
        logger.info(f"[DEBUG] Cancel response: {response.status_code}")
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.error(f"[DEBUG] Cancel error: {e}")
        return False


# =============================================================================
# Async Webhook Delivery Tasks
# =============================================================================

def deliver_normal_webhook(order_id: str, payment_key: str, amount: int):
    """Deliver webhook with minimal delay (normal flow)"""
    delivery = WebhookDelivery(
        id=f"normal_{order_id}_{int(time.time()*1000)}",
        order_id=order_id,
        payment_key=payment_key,
        webhook_type="NORMAL",
        scheduled_at=time.time()
    )
    
    # Minimal delay for normal webhook
    time.sleep(0.1)
    
    payload = create_payment_done_payload(order_id, payment_key, amount)
    success = send_webhook_to_web(payload, delivery)
    
    if success:
        record_stat("normal_delivered")
    
    return success


def deliver_delayed_webhook(order_id: str, payment_key: str, amount: int, delay_s: float):
    """
    Deliver webhook after specified delay.
    This simulates webhook arriving AFTER order timeout.
    """
    delivery = WebhookDelivery(
        id=f"delayed_{order_id}_{int(time.time()*1000)}",
        order_id=order_id,
        payment_key=payment_key,
        webhook_type="DELAYED",
        scheduled_at=time.time()
    )
    
    logger.info(f"[DEBUG] Scheduling delayed webhook for order {order_id}, delay={delay_s}s")
    
    # Wait for specified delay
    time.sleep(delay_s)
    
    payload = create_payment_done_payload(order_id, payment_key, amount)
    success = send_webhook_to_web(payload, delivery)
    
    if success:
        record_stat("delayed_delivered")
    
    return success


def deliver_out_of_order_webhook(order_id: str, payment_key: str, amount: int):
    """
    Deliver CANCEL webhook before CONFIRM webhook.
    Tests state machine handling of out-of-order events.
    """
    logger.info(f"[DEBUG] Starting out-of-order scenario for order {order_id}")
    
    # First: Send CANCEL webhook
    cancel_delivery = WebhookDelivery(
        id=f"ooo_cancel_{order_id}_{int(time.time()*1000)}",
        order_id=order_id,
        payment_key=payment_key,
        webhook_type="OUT_OF_ORDER_CANCEL",
        scheduled_at=time.time()
    )
    
    cancel_payload = create_payment_canceled_payload(order_id, payment_key, "Out-of-order test")
    send_webhook_to_web(cancel_payload, cancel_delivery)
    
    # Small delay between webhooks
    time.sleep(0.2)
    
    # Second: Send DONE webhook (arrives after cancel)
    done_delivery = WebhookDelivery(
        id=f"ooo_done_{order_id}_{int(time.time()*1000)}",
        order_id=order_id,
        payment_key=payment_key,
        webhook_type="OUT_OF_ORDER_DONE",
        scheduled_at=time.time()
    )
    
    done_payload = create_payment_done_payload(order_id, payment_key, amount)
    success = send_webhook_to_web(done_payload, done_delivery)
    
    if success:
        record_stat("out_of_order_delivered")
    
    return success


def deliver_duplicate_webhook(order_id: str, payment_key: str, amount: int, count: int = 3):
    """
    Deliver same webhook multiple times.
    Tests idempotency handling.
    """
    logger.info(f"[DEBUG] Starting duplicate scenario for order {order_id}, count={count}")
    
    payload = create_payment_done_payload(order_id, payment_key, amount)
    results = []
    
    for i in range(count):
        delivery = WebhookDelivery(
            id=f"dup_{order_id}_{i}_{int(time.time()*1000)}",
            order_id=order_id,
            payment_key=payment_key,
            webhook_type=f"DUPLICATE_{i+1}",
            scheduled_at=time.time()
        )
        
        success = send_webhook_to_web(payload, delivery)
        results.append(success)
        
        # Small delay between duplicates
        time.sleep(0.1)
    
    record_stat("duplicate_delivered")
    return results


# =============================================================================
# API Endpoints
# =============================================================================

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "webhook-sender",
        "uptime_seconds": time.time() - _stats["service_start_time"]
    })


@app.route("/simulate", methods=["POST"])
def simulate_webhook():
    """
    Main endpoint for Locust to request webhook simulation.
    
    Request body:
    {
        "order_id": "123",
        "payment_key": "pay_xxx",
        "type": "NORMAL | DELAYED | OUT_OF_ORDER | DUPLICATE",
        "delay_s": 5,  // for DELAYED type
        "amount": 10000,
        "duplicate_count": 3  // for DUPLICATE type
    }
    """
    record_stat("requests_received")
    
    try:
        data = request.json
        order_id = str(data.get("order_id", ""))
        payment_key = data.get("payment_key", "")
        webhook_type = data.get("type", "NORMAL").upper()
        delay_s = float(data.get("delay_s", 5))
        amount = int(data.get("amount", 10000))
        duplicate_count = int(data.get("duplicate_count", 3))
        
        if not order_id or not payment_key:
            return jsonify({"error": "order_id and payment_key required"}), 400
        
        logger.info(f"[DEBUG] Received simulation request: type={webhook_type}, order={order_id}")
        
        if webhook_type == "NORMAL":
            record_stat("normal_webhooks_scheduled")
            executor.submit(deliver_normal_webhook, order_id, payment_key, amount)
            
        elif webhook_type == "DELAYED":
            record_stat("delayed_webhooks_scheduled")
            executor.submit(deliver_delayed_webhook, order_id, payment_key, amount, delay_s)
            
        elif webhook_type == "OUT_OF_ORDER":
            record_stat("out_of_order_webhooks_scheduled")
            executor.submit(deliver_out_of_order_webhook, order_id, payment_key, amount)
            
        elif webhook_type == "DUPLICATE":
            record_stat("duplicate_webhooks_scheduled")
            executor.submit(deliver_duplicate_webhook, order_id, payment_key, amount, duplicate_count)
            
        else:
            return jsonify({"error": f"Unknown webhook type: {webhook_type}"}), 400
        
        return jsonify({
            "status": "scheduled",
            "order_id": order_id,
            "type": webhook_type,
            "message": f"Webhook simulation scheduled for order {order_id}"
        })
        
    except Exception as e:
        logger.error(f"[DEBUG] Error in /simulate: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/direct-confirm", methods=["POST"])
def direct_confirm():
    """
    Direct confirm request (bypasses webhook, for testing).
    """
    try:
        data = request.json
        order_id = str(data.get("order_id", ""))
        payment_key = data.get("payment_key", "")
        amount = int(data.get("amount", 10000))
        auth_header = data.get("auth_header", "")
        
        success = send_confirm_to_web(order_id, payment_key, amount, auth_header)
        
        return jsonify({
            "status": "success" if success else "failed",
            "order_id": order_id
        })
        
    except Exception as e:
        logger.error(f"[DEBUG] Error in /direct-confirm: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/direct-cancel", methods=["POST"])
def direct_cancel():
    """
    Direct cancel request (for out-of-order testing).
    """
    try:
        data = request.json
        payment_key = data.get("payment_key", "")
        reason = data.get("reason", "Test cancellation")
        auth_header = data.get("auth_header", "")
        
        success = send_cancel_to_web(payment_key, reason, auth_header)
        
        return jsonify({
            "status": "success" if success else "failed",
            "payment_key": payment_key
        })
        
    except Exception as e:
        logger.error(f"[DEBUG] Error in /direct-cancel: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/stats", methods=["GET"])
def get_stats():
    """Get current webhook delivery statistics"""
    with _stats_lock:
        stats_copy = _stats.copy()
        
        # Calculate averages
        latencies = stats_copy.pop("delivery_latencies_ms", [])
        if latencies:
            stats_copy["avg_latency_ms"] = sum(latencies) / len(latencies)
            stats_copy["max_latency_ms"] = max(latencies)
            stats_copy["min_latency_ms"] = min(latencies)
        else:
            stats_copy["avg_latency_ms"] = 0
            stats_copy["max_latency_ms"] = 0
            stats_copy["min_latency_ms"] = 0
            
        stats_copy["total_latency_samples"] = len(latencies)
        
    return jsonify(stats_copy)


@app.route("/deliveries/<order_id>", methods=["GET"])
def get_order_deliveries(order_id: str):
    """Get all webhook deliveries for a specific order"""
    with _deliveries_lock:
        deliveries = _deliveries.get(order_id, [])
        return jsonify({
            "order_id": order_id,
            "delivery_count": len(deliveries),
            "deliveries": [
                {
                    "id": d.id,
                    "type": d.webhook_type,
                    "scheduled_at": d.scheduled_at,
                    "delivered_at": d.delivered_at,
                    "response_code": d.response_code,
                    "success": d.success,
                    "error_message": d.error_message,
                    "attempt_count": d.attempt_count
                }
                for d in deliveries
            ]
        })


@app.route("/reset", methods=["POST"])
def reset_stats():
    """Reset all statistics"""
    global _stats, _deliveries
    
    with _stats_lock:
        _stats = {
            "service_start_time": time.time(),
            "requests_received": 0,
            "normal_webhooks_scheduled": 0,
            "delayed_webhooks_scheduled": 0,
            "out_of_order_webhooks_scheduled": 0,
            "duplicate_webhooks_scheduled": 0,
            "webhooks_delivered": 0,
            "webhooks_failed": 0,
            "delivery_latencies_ms": [],
            "normal_delivered": 0,
            "delayed_delivered": 0,
            "out_of_order_delivered": 0,
            "duplicate_delivered": 0,
            "response_200": 0,
            "response_400": 0,
            "response_401": 0,
            "response_409": 0,
            "response_500": 0,
            "response_other": 0,
        }
    
    with _deliveries_lock:
        _deliveries.clear()
    
    return jsonify({"status": "reset", "message": "Statistics cleared"})


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    logger.info(f"Starting Webhook Sender Service on port {port}")
    logger.info(f"Target web service: {WEB_SERVICE_URL}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
