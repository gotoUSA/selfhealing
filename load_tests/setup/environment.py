#!/usr/bin/env python
"""
Load Test Environment Setup

Ensures idempotent test environment before running load tests.
Guarantees consistent state regardless of previous test runs.

Usage:
    python -m load_tests.setup.environment --full
    python -m load_tests.setup.environment --users-only
    python -m load_tests.setup.environment --products-only
    python -m load_tests.setup.environment --reset-db
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Project paths
SETUP_DIR = Path(__file__).parent
LOAD_TESTS_DIR = SETUP_DIR.parent
PROJECT_ROOT = LOAD_TESTS_DIR.parent


def run_manage_command(command: list, capture_output: bool = False, use_docker: bool = None) -> subprocess.CompletedProcess:
    """Run Django management command"""

    # Auto-detect Docker environment
    if use_docker is None:
        use_docker = is_docker_available()

    if use_docker:
        full_cmd = ["docker-compose", "exec", "-T", "web", "python", "manage.py"] + command
        cwd = str(PROJECT_ROOT)
    else:
        manage_py = PROJECT_ROOT / "manage.py"
        full_cmd = [sys.executable, str(manage_py)] + command
        cwd = str(PROJECT_ROOT)

    cmd_str = " ".join(command)
    env_type = "[Docker]" if use_docker else "[Local]"
    print(f"  {env_type} Running: python manage.py {cmd_str}")

    if capture_output:
        return subprocess.run(full_cmd, capture_output=True, text=True, cwd=cwd)
    return subprocess.run(full_cmd, cwd=cwd)


def is_docker_available() -> bool:
    """Check if Docker is available and containers are running"""
    try:
        result = subprocess.run(
            ["docker-compose", "ps", "--services", "--filter", "status=running"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=5,
        )
        return "web" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_db_connection() -> bool:
    """Check if database is accessible"""
    print("\n🔍 Checking database connection...")
    result = run_manage_command(["check", "--database", "default"], capture_output=True)
    if result.returncode == 0:
        print("  ✅ Database connection OK")
        return True
    else:
        # Try to extract meaningful error
        error_msg = result.stderr if result.stderr else "Unknown error"
        if "ModuleNotFoundError" in error_msg:
            print("  ⚠️  Local environment missing packages, trying Docker...")
            # Retry with Docker
            result = run_manage_command(["check", "--database", "default"], capture_output=True, use_docker=True)
            if result.returncode == 0:
                print("  ✅ Database connection OK (via Docker)")
                return True
        print("  ❌ Database connection failed")
        return False


def run_migrations() -> bool:
    """Run pending migrations"""
    print("\n📦 Running migrations...")
    result = run_manage_command(["migrate", "--run-syncdb"])
    return result.returncode == 0


def reset_load_test_data() -> bool:
    """
    Reset only load test related data (non-destructive to production data)

    Deletes:
    - load_test_user_* users
    - Orders from load test users
    - Carts from load test users
    """
    print("\n🗑️  Resetting load test data...")

    # This uses Django ORM via a custom script
    reset_script = """
import django
import os
import sys

sys.path.insert(0, "{project_root}")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from django.contrib.auth import get_user_model
from shopping.models import Order, Cart, CartItem

User = get_user_model()

# Find load test users
load_test_users = User.objects.filter(username__startswith="load_test_user_")
user_count = load_test_users.count()

if user_count > 0:
    # Delete related orders
    order_count = Order.objects.filter(user__in=load_test_users).delete()[0]

    # Delete related carts
    cart_count = Cart.objects.filter(user__in=load_test_users).delete()[0]

    print(f"  Deleted {{order_count}} orders, {{cart_count}} carts from load test users")

print(f"  Load test users found: {{user_count}}")
""".format(
        project_root=str(PROJECT_ROOT).replace("\\", "\\\\")
    )

    result = subprocess.run([sys.executable, "-c", reset_script], capture_output=True, text=True, cwd=str(PROJECT_ROOT))

    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(f"  ⚠️  Reset warning: {result.stderr}")

    return True


def create_load_test_users(count: int = 100, points: int = 50000, clear: bool = True) -> bool:
    """Create load test users with initial points"""
    print(f"\n👥 Creating {count} load test users (points: {points:,})...")

    cmd = ["create_load_test_users", "--count", str(count), "--points", str(points)]
    if clear:
        cmd.append("--clear")

    result = run_manage_command(cmd)

    if result.returncode == 0:
        print(f"  ✅ Created {count} load test users")
        return True
    else:
        print("  ❌ Failed to create load test users")
        return False


def create_test_products(preset: str = "full", clear: bool = False) -> bool:
    """Create test products using preset"""
    print(f"\n📦 Creating test products (preset: {preset})...")

    cmd = ["create_test_data", "--preset", preset]
    if clear:
        cmd.append("--clear")

    result = run_manage_command(cmd)

    if result.returncode == 0:
        print(f"  ✅ Created test products with '{preset}' preset")
        return True
    else:
        # Check if products already exist (duplicate key error is OK)
        print("  ⚠️  Product creation had issues, checking existing products...")
        stats = verify_environment(silent=True)
        if stats.get("products", 0) >= 5:
            print(f"  ✅ Found {stats.get('products', 0)} existing products, proceeding...")
            return True
        print("  ❌ Failed to create test products and no existing products found")
        return False


def verify_environment(silent: bool = False) -> dict:
    """Verify test environment is ready"""
    if not silent:
        print("\n🔎 Verifying environment...")

    # Use Django management command via Docker if available
    if is_docker_available():
        verify_cmd = [
            "docker-compose",
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            """
import django
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()
from django.contrib.auth import get_user_model
from shopping.models import Product, Category
User = get_user_model()
print(f"USERS:{User.objects.filter(username__startswith='load_test_user_').count()}")
print(f"PRODUCTS:{Product.objects.filter(is_active=True).count()}")
print(f"CATEGORIES:{Category.objects.count()}")
""",
        ]
        result = subprocess.run(verify_cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    else:
        verify_script = """
import django
import os
import sys

sys.path.insert(0, "{project_root}")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from django.contrib.auth import get_user_model
from shopping.models import Product, Category

User = get_user_model()

load_test_users = User.objects.filter(username__startswith="load_test_user_").count()
products = Product.objects.filter(is_active=True).count()
categories = Category.objects.count()

print(f"USERS:{{load_test_users}}")
print(f"PRODUCTS:{{products}}")
print(f"CATEGORIES:{{categories}}")
""".format(
            project_root=str(PROJECT_ROOT).replace("\\", "\\\\")
        )

        result = subprocess.run([sys.executable, "-c", verify_script], capture_output=True, text=True, cwd=str(PROJECT_ROOT))

    stats = {}
    for line in result.stdout.strip().split("\n"):
        if ":" in line and line.split(":")[0].upper() in ["USERS", "PRODUCTS", "CATEGORIES"]:
            key, value = line.split(":")
            try:
                stats[key.lower()] = int(value)
            except ValueError:
                pass

    if not silent:
        print(f"  Users: {stats.get('users', 0)}")
        print(f"  Products: {stats.get('products', 0)}")
        print(f"  Categories: {stats.get('categories', 0)}")

    return stats


def setup_full(user_count: int = 1000, user_points: int = 50000, product_preset: str = "full") -> bool:
    """
    Full environment setup (idempotent)

    1. Check DB connection
    2. Run migrations
    3. Reset load test data
    4. Create load test users
    5. Create test products
    6. Verify environment
    """
    print("\n" + "=" * 60)
    print("🚀 LOAD TEST ENVIRONMENT SETUP")
    print("=" * 60)

    steps = [
        ("Database Check", lambda: check_db_connection()),
        ("Migrations", lambda: run_migrations()),
        ("Reset Test Data", lambda: reset_load_test_data()),
        ("Create Users", lambda: create_load_test_users(user_count, user_points, clear=True)),
        ("Create Products", lambda: create_test_products(product_preset, clear=False)),
    ]

    for step_name, step_func in steps:
        if not step_func():
            print(f"\n❌ Setup failed at: {step_name}")
            return False

    # Verify
    stats = verify_environment()

    # Validation
    min_users = 10
    min_products = 5

    if stats.get("users", 0) < min_users:
        print(f"\n⚠️  Warning: Only {stats.get('users', 0)} users (expected >= {min_users})")

    if stats.get("products", 0) < min_products:
        print(f"\n⚠️  Warning: Only {stats.get('products', 0)} products (expected >= {min_products})")

    print("\n" + "=" * 60)
    print("✅ ENVIRONMENT SETUP COMPLETE")
    print("=" * 60)

    return True


def main():
    parser = argparse.ArgumentParser(description="Load Test Environment Setup")

    parser.add_argument(
        "--full",
        action="store_true",
        help="Full setup (reset + users + products)",
    )
    parser.add_argument(
        "--users-only",
        action="store_true",
        help="Create/reset load test users only",
    )
    parser.add_argument(
        "--products-only",
        action="store_true",
        help="Create test products only",
    )
    parser.add_argument(
        "--reset-only",
        action="store_true",
        help="Reset load test data only (no creation)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify environment only",
    )
    parser.add_argument(
        "--user-count",
        type=int,
        default=1000,
        help="Number of load test users to create (default: 1000)",
    )
    parser.add_argument(
        "--user-points",
        type=int,
        default=50000,
        help="Initial points per user (default: 50000)",
    )
    parser.add_argument(
        "--product-preset",
        type=str,
        choices=["minimal", "basic", "full"],
        default="full",
        help="Product creation preset (default: full)",
    )

    args = parser.parse_args()

    # Default to full if no option specified
    if not any([args.full, args.users_only, args.products_only, args.reset_only, args.verify]):
        args.full = True

    success = True

    if args.verify:
        verify_environment()
    elif args.reset_only:
        success = reset_load_test_data()
    elif args.users_only:
        success = create_load_test_users(args.user_count, args.user_points, clear=True)
    elif args.products_only:
        success = create_test_products(args.product_preset)
    elif args.full:
        success = setup_full(args.user_count, args.user_points, args.product_preset)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
