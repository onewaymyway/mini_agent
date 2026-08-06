"""
学术资源兼容性测试

测试覆盖：
- 知网、arXiv
- 首页访问、论文搜索、列表提取、详情页、摘要提取、PDF下载
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestCNKI:
    """知网兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P2")
        return next((c for c in configs if c.name == "知网"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "CNKI-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "CNKI-02", "name": "论文搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "CNKI-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "CNKI-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_abstract(self, runner, config):
        scenario = {"id": "CNKI-05", "name": "摘要提取", "action": "extract_abstract", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "知网"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestArxiv:
    """arXiv 兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P2")
        return next((c for c in configs if c.name == "arXiv"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "ARXIV-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "ARXIV-02", "name": "论文搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "ARXIV-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "ARXIV-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_download_pdf(self, runner, config):
        scenario = {"id": "ARXIV-05", "name": "PDF下载", "action": "download_pdf", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "arXiv"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestAcademicIntegration:
    """学术资源集成测试"""

    def test_all_academic_sites(self):
        configs = get_websites_by_priority("P2")
        academic_sites = [c for c in configs if c.category == "学术资源"]
        assert len(academic_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in academic_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
