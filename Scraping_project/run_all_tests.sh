#!/bin/bash
#
# Test Runner - Runs all diagnostic tests in sequence
#

set -e  # Exit on error

echo "================================================================================"
echo "SCRAPY DIAGNOSTIC TEST SUITE"
echo "================================================================================"
echo ""
echo "This script will run 4 diagnostic tests to identify the crawling issue:"
echo ""
echo "  Test 1: Single spider instance (no multiprocessing)"
echo "  Test 2: Verify start_requests() is called"
echo "  Test 3: Minimal URLs test (5 hardcoded URLs)"
echo "  Test 4: Scout spider with limited URLs (10 from Delta Lake)"
echo ""
echo "Press Enter to continue, or Ctrl+C to cancel..."
read

echo ""
echo "================================================================================"
echo "TEST 1: Single Spider Instance"
echo "================================================================================"
echo ""
python test_single_spider.py 2>&1 | tee test1_output.log
echo ""
echo "✅ Test 1 complete. Output saved to: test1_output.log"
echo ""
echo "Press Enter to continue to Test 2..."
read

echo ""
echo "================================================================================"
echo "TEST 2: Verify start_requests() is Called"
echo "================================================================================"
echo ""
python test_start_requests.py 2>&1 | tee test2_output.log
echo ""
echo "✅ Test 2 complete. Output saved to: test2_output.log"
echo ""
echo "Press Enter to continue to Test 3..."
read

echo ""
echo "================================================================================"
echo "TEST 3: Minimal URLs Test"
echo "================================================================================"
echo ""
python test_minimal_urls.py 2>&1 | tee test3_output.log
echo ""
echo "✅ Test 3 complete. Output saved to: test3_output.log"
echo ""
echo "Press Enter to continue to Test 4..."
read

echo ""
echo "================================================================================"
echo "TEST 4: Scout Spider with Limited URLs"
echo "================================================================================"
echo ""
python test_scout_limited.py 2>&1 | tee test4_output.log
echo ""
echo "✅ Test 4 complete. Output saved to: test4_output.log"
echo ""

echo ""
echo "================================================================================"
echo "ALL TESTS COMPLETED"
echo "================================================================================"
echo ""
echo "Test results saved to:"
echo "  - test1_output.log (Single spider)"
echo "  - test2_output.log (start_requests verification)"
echo "  - test3_output.log (Minimal URLs)"
echo "  - test4_output.log (Scout limited)"
echo ""
echo "Next steps:"
echo "  1. Review the logs to identify which test failed"
echo "  2. Compare successful vs failed tests to isolate the issue"
echo "  3. Focus debugging on the specific component that failed"
echo ""
echo "================================================================================"
