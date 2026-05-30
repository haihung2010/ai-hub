# Multi-Project Load Test Design

## Overview

**Goal:** Comprehensive load test 3 concurrent AI Hub projects over 1 hour to verify:
1. System stability under sustained load
2. Per-project context optimization
3. Accuracy and response quality per project type
4. Resource isolation between projects

## Projects

### 1. Fanpage (Multi-tenant Chatbot)
- **2 tenants × 5 users = 10 concurrent users**
- **Tenant A:** sản phẩm咨询, order status, khiếu nại
- **Tenant B:** shipping, payment, refund
- **Context:** 8K (lite mode)
- **Latency target:** <3s avg

### 2. Vehix (Vehicle Fleet Management)
- **5 concurrent users**
- **Queries:** contract lookup, vehicle status, fleet tracking
- **Context:** 16K (normal mode)
- **Latency target:** <2s avg

### 3. IHI (Machining Line Analytics)
- **1 sensor line, 30-40 machines**
- **Check frequency:** every 1 minute
- **Metrics:** energy, vibration, production, idle detection, CO2
- **Context:** 131K (full analysis)
- **Accuracy target:** Detect patterns correctly

## Test Timeline (1 hour)

| Phase | Time | Description |
|-------|------|-------------|
| Warmup | 0-2min | All systems warmup |
| Phase 1 | 2-15min | Light load: 50% target RPS |
| Phase 2 | 15-30min | Medium load: 75% target RPS |
| Phase 3 | 30-45min | Heavy load: 100% target RPS |
| Phase 4 | 45-60min | Sustained max: continuous max load |

## Success Criteria

- All 3 projects respond correctly
- Fanpage: >95% valid JSON responses
- Vehix: >95% correct data lookups
- IHI: Pattern detection accuracy >80%
- No tenant data leakage between projects
- Latency p95 <5s under max load

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    AI Hub Router                       │
├──────────────┬──────────────┬──────────────────────────┤
│   Fanpage    │    Vehix    │          IHI             │
│   (port 8080) │  (port 8080) │     (port 8083)       │
│   8K ctx    │   16K ctx   │      131K ctx          │
│   lite mode  │   normal    │      full analysis       │
└──────────────┴──────────────┴──────────────────────────┘
```

## Test Data

### Fanpage
- Product catalog queries
- Order status checks
- Complaint handling
- Shipping inquiries

### Vehix
- Contract lookups (UUID format)
- Vehicle status (ACTIVE/IDLE/MAINTENANCE)
- Fleet metrics

### IHI
- Machine sensors: power_kW, vibration_mm_s, temperature_c, production_units
- Compressed JSON format: `"M001:T30.5,V1.2,P8.5,E0.8"`
- 30-40 machines per check

## Metrics Collected

- Request latency (avg, p50, p95, p99)
- Error rate per project
- Token usage
- Context switch overhead
- Memory/CPU per llama.cpp instance