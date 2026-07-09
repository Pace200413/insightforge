# Failure Analysis

## Observed SQL generation failures (from `make eval`, 2026-07-09)

The SQL generator (Llama 3.3 70B via Groq) produced queries that failed at
execution in ~40% of complex steps. The query-repair agent recovered most.

| Step | Error | Repair outcome |
|------|-------|----------------|
| `by_product` | `column "revenue_change" does not exist` (referenced an alias defined in the same SELECT) | Repair produced a parse error, firewall correctly blocked it |
| `refund_check` | `column c.category_id does not exist` (wrong join path) | Fixed on attempt 1 |
| `order_volume_check` | `aggregate function calls cannot be nested`, then `column "order_id" is ambiguous` | Fixed on attempt 2 |

## Root causes
1. The model references SELECT-list aliases in the same SELECT (not valid SQL).
2. It occasionally guesses join keys (`c.category_id`) instead of following FKs.
3. Nested aggregates and ambiguous columns in multi-join queries.

## Mitigations implemented
- Query-repair agent with up to 3 attempts (recovered 2/3 failures here).
- SQL firewall blocks malformed repairs instead of executing them.

## Planned improvements
- Add few-shot examples of correct multi-join revenue queries to the generator prompt.
- Post-process: reject queries that reference undefined aliases before execution.
- Try a stronger SQL model for generation.
