# Missing Implementation Features

## RateMyProfessor Integration (Documented but Not Implemented)

The README documents a comprehensive RateMyProfessor integration plan, but **no actual code exists** to fetch or match this data yet.

### What's Missing:
- No RateMyProfessor fetcher/scraper implementation
- No faculty name matching algorithms (fuzzy matching, Levenshtein, etc.)
- No RMP data storage schema or pipeline integration
- No ToS-compliant rate limiting for RMP requests
- No opt-out or manual review capabilities

### Expect Missing Functionality Until:
- Dedicated RMP fetcher classes are implemented
- Faculty matching logic is built
- RMP data integration is added to Stage 3 enrichment
- Compliance and ethics safeguards are coded

### Current Status:
This is planned feature documentation only - attempting to use RMP functionality will fail since the implementation doesn't exist.