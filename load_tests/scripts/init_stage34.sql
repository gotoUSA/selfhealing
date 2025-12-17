-- Stage 34: DB Deadlock Test - Database Initialization Script
-- 이 스크립트는 Deadlock 테스트를 위한 초기 데이터를 설정합니다.

-- 테스트용 상품 테이블이 없으면 생성
CREATE TABLE IF NOT EXISTS test_products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    stock INTEGER NOT NULL DEFAULT 1000,
    price DECIMAL(10, 2) NOT NULL DEFAULT 10000.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 테스트용 사용자 잔액 테이블
CREATE TABLE IF NOT EXISTS test_user_balances (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(100) UNIQUE NOT NULL,
    balance DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    points INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 테스트용 주문 테이블
CREATE TABLE IF NOT EXISTS test_orders (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(100) UNIQUE NOT NULL,
    user_id VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    total_amount DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 테스트용 주문 아이템 테이블
CREATE TABLE IF NOT EXISTS test_order_items (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(100) NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 초기 상품 데이터 (10개)
INSERT INTO test_products (name, stock, price) VALUES
    ('Product 0', 1000, 10000.00),
    ('Product 1', 1000, 15000.00),
    ('Product 2', 1000, 20000.00),
    ('Product 3', 1000, 25000.00),
    ('Product 4', 1000, 30000.00),
    ('Product 5', 1000, 35000.00),
    ('Product 6', 1000, 40000.00),
    ('Product 7', 1000, 45000.00),
    ('Product 8', 1000, 50000.00),
    ('Product 9', 1000, 55000.00)
ON CONFLICT DO NOTHING;

-- Deadlock 모니터링을 위한 함수
CREATE OR REPLACE FUNCTION log_deadlock_event()
RETURNS TRIGGER AS $$
BEGIN
    RAISE NOTICE 'Deadlock detected on table %', TG_TABLE_NAME;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- 재고 업데이트 함수 (락 경합 테스트용)
CREATE OR REPLACE FUNCTION update_stock_with_lock(
    p_product_id INTEGER,
    p_quantity INTEGER
) RETURNS BOOLEAN AS $$
DECLARE
    v_current_stock INTEGER;
BEGIN
    -- 락 획득 (FOR UPDATE)
    SELECT stock INTO v_current_stock
    FROM test_products
    WHERE id = p_product_id
    FOR UPDATE;

    IF v_current_stock IS NULL THEN
        RAISE EXCEPTION 'Product not found: %', p_product_id;
    END IF;

    IF v_current_stock + p_quantity < 0 THEN
        RAISE EXCEPTION 'Insufficient stock: % (current: %, delta: %)',
            p_product_id, v_current_stock, p_quantity;
    END IF;

    UPDATE test_products
    SET stock = stock + p_quantity,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_product_id;

    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- 잔액 업데이트 함수 (락 경합 테스트용)
CREATE OR REPLACE FUNCTION update_balance_with_lock(
    p_user_id VARCHAR(100),
    p_delta DECIMAL(15, 2)
) RETURNS BOOLEAN AS $$
DECLARE
    v_current_balance DECIMAL(15, 2);
BEGIN
    -- 락 획득 (FOR UPDATE)
    SELECT balance INTO v_current_balance
    FROM test_user_balances
    WHERE user_id = p_user_id
    FOR UPDATE;

    IF v_current_balance IS NULL THEN
        -- 새 사용자 생성
        INSERT INTO test_user_balances (user_id, balance)
        VALUES (p_user_id, GREATEST(0, p_delta));
        RETURN TRUE;
    END IF;

    IF v_current_balance + p_delta < 0 THEN
        RAISE EXCEPTION 'Insufficient balance: % (current: %, delta: %)',
            p_user_id, v_current_balance, p_delta;
    END IF;

    UPDATE test_user_balances
    SET balance = balance + p_delta,
        updated_at = CURRENT_TIMESTAMP
    WHERE user_id = p_user_id;

    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- 포인트 업데이트 함수
CREATE OR REPLACE FUNCTION update_points_with_lock(
    p_user_id VARCHAR(100),
    p_delta INTEGER
) RETURNS BOOLEAN AS $$
DECLARE
    v_current_points INTEGER;
BEGIN
    SELECT points INTO v_current_points
    FROM test_user_balances
    WHERE user_id = p_user_id
    FOR UPDATE;

    IF v_current_points IS NULL THEN
        INSERT INTO test_user_balances (user_id, points)
        VALUES (p_user_id, GREATEST(0, p_delta));
        RETURN TRUE;
    END IF;

    IF v_current_points + p_delta < 0 THEN
        RAISE EXCEPTION 'Insufficient points: % (current: %, delta: %)',
            p_user_id, v_current_points, p_delta;
    END IF;

    UPDATE test_user_balances
    SET points = points + p_delta,
        updated_at = CURRENT_TIMESTAMP
    WHERE user_id = p_user_id;

    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_test_products_name ON test_products(name);
CREATE INDEX IF NOT EXISTS idx_test_orders_user_id ON test_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_test_orders_status ON test_orders(status);
CREATE INDEX IF NOT EXISTS idx_test_order_items_order_id ON test_order_items(order_id);

-- 권한 설정
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO postgres;

-- 완료 메시지
DO $$
BEGIN
    RAISE NOTICE 'Stage 34 database initialization completed successfully!';
END $$;
