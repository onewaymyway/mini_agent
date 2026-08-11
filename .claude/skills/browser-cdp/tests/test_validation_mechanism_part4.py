# ============================================================================
# Main Test Class
# ============================================================================

class TestValidationMechanism:
    """Main test class for validation mechanism"""

    def test_all_test_cases_defined(self):
        """Test all test cases are defined"""
        assert len(ALL_TEST_CASES) == 119, f"Expected 119 test cases, got {len(ALL_TEST_CASES)}"
        assert len(P0_TEST_CASES) == 44, f"Expected 44 P0 cases, got {len(P0_TEST_CASES)}"
        assert len(P1_TEST_CASES) == 18, f"Expected 18 P1 cases, got {len(P1_TEST_CASES)}"
        assert len(P2_TEST_CASES) == 18, f"Expected 18 P2 cases, got {len(P2_TEST_CASES)}"

    def test_test_case_structure(self):
        """Test test case structure is correct"""
        for case in ALL_TEST_CASES:
            assert case.case_id, "case_id cannot be empty"
            assert case.name, "name cannot be empty"
            assert case.website, "website cannot be empty"
            assert case.capability.startswith("OP-"), f"Invalid capability format: {case.capability}"
            assert case.priority in ["P0", "P1", "P2"], f"Invalid priority: {case.priority}"
            assert case.action, "action cannot be empty"

    def test_capability_coverage(self):
        """Test capability coverage completeness"""
        capabilities = set(tc.capability for tc in ALL_TEST_CASES)
        expected_capabilities = set(CoverageValidator.CAPABILITY_WEBSITE_MATRIX.keys())
        assert capabilities == expected_capabilities, f"Missing capabilities: {expected_capabilities - capabilities}"

    def test_priority_distribution(self):
        """Test priority distribution is reasonable"""
        p0_count = sum(1 for tc in ALL_TEST_CASES if tc.priority == "P0")
        p1_count = sum(1 for tc in ALL_TEST_CASES if tc.priority == "P1")
        p2_count = sum(1 for tc in ALL_TEST_CASES if tc.priority == "P2")
        assert p0_count >= 40, f"P0 cases insufficient: {p0_count}"
        assert p1_count >= 15, f"P1 cases insufficient: {p1_count}"
        assert p2_count >= 15, f"P2 cases insufficient: {p2_count}"

    def test_execution_engine_mock_mode(self):
        """Test mock mode execution engine"""
        engine = TestExecutionEngine(mock_mode=True)
        results = engine.execute_cases(ALL_TEST_CASES[:10])
        assert len(results) == 10
        assert all(r.success for r in results), "All P0 cases should pass in mock mode"

    def test_execution_engine_report(self):
        """Test report generation"""
        engine = TestExecutionEngine(mock_mode=True)
        engine.execute_cases(ALL_TEST_CASES)
        report = engine.generate_report()
        assert report["total_cases"] == 119
        assert report["passed_cases"] > 0
        assert "capability_scores" in report
        assert len(report["capability_scores"]) == 24

    def test_coverage_validator(self):
        """Test coverage validation"""
        validator = CoverageValidator()
        coverage = validator.validate_coverage(ALL_TEST_CASES)
        assert "capability_coverage" in coverage
        assert "website_coverage" in coverage
        assert "priority_coverage" in coverage
        assert len(coverage["capability_coverage"]) == 24

    def test_capability_score_calculation(self):
        """Test capability score calculation"""
        engine = TestExecutionEngine(mock_mode=True)
        engine.execute_cases(ALL_TEST_CASES)
        scores = engine.get_capability_scores()
        for cap_id, score in scores.items():
            assert score.total_tests > 0, f"{cap_id} should have test cases"
            assert 0 <= score.success_rate <= 100
            assert 0 <= score.avg_score <= 100

    def test_website_evaluation(self):
        """Test website evaluation"""
        engine = TestExecutionEngine(mock_mode=True)
        engine.execute_cases(ALL_TEST_CASES)
        report = engine.generate_report()
        assert "report_id" in report
        assert "generated_at" in report
        assert "overall_success_rate" in report
        assert "avg_score" in report


# ============================================================================
# CLI Entry Point
# ============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Website Operation Capability Test Validation")
    parser.add_argument("--real-browser", action="store_true", help="Use real browser for testing")
    parser.add_argument("--output", "-o", default="test_report.json", help="Output report path")
    args = parser.parse_args()

    engine = TestExecutionEngine(mock_mode=not args.real_browser)
    results = engine.execute_cases(ALL_TEST_CASES)
    report = engine.generate_report()

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Test report saved to: {args.output}")
    print(f"Total cases: {report['total_cases']}")
    print(f"Passed: {report['passed_cases']}")
    print(f"Success rate: {report['overall_success_rate']}%")
    print(f"Average score: {report['avg_score']}")
