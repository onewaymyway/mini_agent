"""
视频平台兼容性测试

测试覆盖：
- B站
- 首页访问、视频搜索、列表提取、详情页、评论提取
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestBilibili:
    """B站兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P2")
        return next((c for c in configs if c.name == "B站"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "BILIBILI-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "BILIBILI-02", "name": "视频搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "BILIBILI-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "BILIBILI-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_comments(self, runner, config):
        scenario = {"id": "BILIBILI-05", "name": "评论提取", "action": "extract_comments", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "B站"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestVideoIntegration:
    """视频平台集成测试"""

    def test_all_video_sites(self):
        configs = get_websites_by_priority("P2")
        video_sites = [c for c in configs if c.category == "视频平台"]
        assert len(video_sites) >= 1

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in video_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
