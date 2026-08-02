# -*- coding: utf-8 -*-
"""
金融舆情分析模块
基于词典法的轻量级金融情感分析
==================================================

功能：
- 文本清洗与分词
- 金融领域情感词典
- 否定词/程度副词处理
- 实体识别 (股票代码、金额、时间)
- 关键词提取
- 批量分析股吧/新闻文本
- 股票舆情聚合与信号生成

依赖：jieba (pip install jieba)
符合 finance-data-toolkit 统一数据契约
"""

import re
import json
import jieba
import jieba.posseg as pseg
import jieba.analyse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from collections import Counter


# ============== 数据结构 ==============

@dataclass
class SentimentResult:
    """情感分析结果"""
    text: str
    score: float                    # [-1, 1] 负面到正面
    label: str                      # VERY_POSITIVE / POSITIVE / NEUTRAL / NEGATIVE / VERY_NEGATIVE
    confidence: float               # 置信度 [0, 1]
    model: str                      # 'lexicon'
    timestamp: str                  # ISO 格式
    entities: List[Dict] = None     # 识别出的实体
    keywords: List[str] = None      # 关键词
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FinanceData:
    """统一金融数据契约"""
    source: str
    data_type: str
    symbol: str
    timestamp: str
    payload: Dict[str, Any]
    raw: Optional[Dict] = None
    meta: Optional[Dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ============== 情感词典 ==============

# 金融领域正面词汇
FINANCE_POSITIVE = {
    '上涨', '涨停', '大涨', '飙升', '暴涨', '拉升', '反弹', '突破', '创新高', '走强',
    '上扬', '攀升', '增长', '增加', '扩大', '提升', '改善', '好转', '向好', '乐观',
    '看好', '买入', '增持', '推荐', '强烈推荐', '超配', '买入评级', '目标价上调',
    '业绩增长', '业绩预增', '超预期', '利好', '利好消息', '重大利好', '政策利好',
    '分红', '送转', '高送转', '回购', '增持', '股东增持', '机构买入', '北向资金买入',
    '主力买入', '资金流入', '净流入', '大单买入', '龙虎榜买入', '机构席位买入',
    '低估值', '低市盈率', '高股息', '价值投资', '核心资产', '龙头股', '白马股',
    '护城河', '护城河宽', '护城河深', '成长性好', '成长空间大', '业绩确定性强',
    '现金流充沛', '分红慷慨', '股东回报好', '治理良好', '管理层优秀',
    '行业龙头', '市场份额第一', '竞争优势', '技术领先', '专利多', '研发投入大',
    '订单饱满', '产能扩张', '新产品', '新业务', '新增长点', '拐点', '拐点已现',
    '基本面改善', '基本面向好', '估值修复', '估值提升', '重估', '戴维斯双击',
}

# 金融领域负面词汇
FINANCE_NEGATIVE = {
    '下跌', '跌停', '大跌', '暴跌', '下挫', '回调', '破位', '跌破', '创新低', '走弱',
    '下行', '下滑', '减少', '缩小', '下降', '恶化', '转差', '向坏', '悲观', '看空',
    '卖出', '减持', '清仓', '降级', '下调评级', '目标价下调', '业绩下滑', '业绩预减',
    '不及预期', '利空', '利空消息', '重大利空', '政策利空', '违规', '违规减持',
    '股东减持', '大股东减持', '高管减持', '机构卖出', '北向资金卖出', '主力卖出',
    '资金流出', '净流出', '大单卖出', '龙虎榜卖出', '机构席位卖出', '融资余额下降',
    '高估值', '高市盈率', '泡沫', '估值过高', '估值泡沫', '基本面恶化', '基本面变差',
    '业绩暴雷', '业绩雷', '财务造假', '违规担保', '资金占用', '关联交易', '内控缺陷',
    '退市风险', 'ST', '*ST', '面值退市', '亏损', '巨亏', '债务违约', '债务危机',
    '现金流枯竭', '应收账款高', '存货积压', '产能过剩', '价格战', '毛利率下降',
    '市场份额下降', '竞争加剧', '技术落后', '产品老化', '订单减少', '产能利用率低',
    '行业下行', '行业衰退', '政策收紧', '监管趋严', '行业增速放缓',
}

# 否定词
NEGATION_WORDS = {
    '不', '没', '无', '非', '未', '别', '莫', '勿', '休', '免',
    '不是', '没有', '不曾', '未曾', '从不', '决不', '绝不', '毫不',
    '不再', '不更', '不曾', '未曾', '无法', '不能', '不愿', '不想',
    '难以', '难', '无法', '不可能', '不大可能', '不太可能',
}

# 程度副词
DEGREE_WORDS = {
    '极其': 2.0, '非常': 1.8, '特别': 1.7, '十分': 1.6, '很': 1.3,
    '比较': 1.2, '较': 1.1, '有点': 0.8, '略微': 0.6, '稍微': 0.5,
    '相当': 1.5, '极度': 2.0, '超级': 2.0, '特': 1.7,
    '蛮': 1.2, '挺': 1.2, '老': 1.1, '死': 1.5,
}

# 转折词
TRANSITION_WORDS = {
    '但是', '可是', '不过', '却', '反而', '倒是', '偏偏', '偏',
    '虽然', '尽管', '即使', '纵然', '纵使', '哪怕', '除非',
    '不过是', '只不过', '仅仅', '只', '只有',
}


# ============== 文本预处理 ==============

class TextProcessor:
    """文本预处理器"""
    
    def __init__(self):
        finance_terms = FINANCE_POSITIVE | FINANCE_NEGATIVE
        for term in finance_terms:
            jieba.add_word(term)
        
        extra_terms = [
            '市盈率', '市净率', '股息率', '市销率', '市现率', 'PEG',
            'ROE', 'ROA', 'ROIC', '毛利率', '净利率', '资产负债率',
            '流动比率', '速动比率', '现金流', '自由现金流', 'EBITDA',
            '每股收益', '每股净资产', '每股现金流', '分红率', '送转率',
            '北向资金', '融资余额', '融券余额', '两融余额', '换手率',
            '量比', '委比', '委差', '振幅', '涨跌幅', '涨跌额',
            '均线', 'MA5', 'MA10', 'MA20', 'MA30', 'MA60', 'MA120', 'MA250',
            'EMA', 'MACD', 'DIF', 'DEA', 'RSI', 'KDJ', 'BOLL', '布林带',
            '支撑位', '压力位', '趋势线', '颈线', '头肩顶', '头肩底',
            '双顶', '双底', '箱体', '三角形', '楔形', '旗形',
            '龙虎榜', '营业部', '游资', '机构', '北向', '陆股通', '港股通',
            '沪股通', '深股通', 'AH溢价', 'AH折价', '估值溢价',
        ]
        for term in extra_terms:
            jieba.add_word(term)
    
    def clean(self, text: str) -> str:
        if not text:
            return ''
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'@\w+', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]', '', text)
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
        return text.strip()
    
    def segment(self, text: str, with_pos: bool = False) -> List:
        if with_pos:
            return [(w.word, w.flag) for w in pseg.cut(text)]
        return list(jieba.cut(text))
    
    def extract_keywords(self, text: str, top_k: int = 10, with_weight: bool = False) -> List:
        return jieba.analyse.extract_tags(text, topK=top_k, withWeight=with_weight)
    
    def extract_finance_entities(self, text: str) -> List[Dict]:
        entities = []
        
        # 股票代码: 600000.SH 或 000001.SZ
        for match in re.finditer(r'\b(\d{6})\.(SH|SZ|BJ)\b', text):
            entities.append({'type': 'stock', 'code': f"{match.group(1)}.{match.group(2)}", 'span': match.span()})
        
        # 纯6位数字代码
        for match in re.finditer(r'(?<!\d)\b(\d{6})\b(?!\.\w)', text):
            code = match.group(1)
            if code.startswith(('60', '68', '90', '00', '30', '20')):
                entities.append({'type': 'stock', 'code': code, 'span': match.span()})
        
        # 金额
        for match in re.finditer(r'(\d+(?:\.\d+)?)\s*(亿|万|万元|亿元|%|百分点)', text):
            entities.append({'type': 'amount', 'value': match.group(1), 'unit': match.group(2), 'span': match.span()})
        
        # 时间
        for match in re.finditer(r'(\d{4}年\d{1,2}月|\d{1,2}月\d{1,2}日|Q[1-4]|半年报|年报|季报|一季报|中报|三季报)', text):
            entities.append({'type': 'time', 'value': match.group(1), 'span': match.span()})
        
        # 概念板块
        concepts = ['人工智能', 'AI', '芯片', '半导体', '新能源', '光伏', '风电', '锂电',
                   '储能', '氢能', '碳中和', '碳达峰', '数字经济', '元宇宙', '区块链',
                   '5G', '6G', '物联网', '云计算', '大数据', '工业互联网', '智能制造',
                   '生物医药', '创新药', 'CXO', 'CRO', 'CDMO', '疫苗', '抗体', 'ADC',
                   '军工', '国防', '航天', '卫星', '低空经济', 'eVTOL', '商业航天',
                   '消费电子', '折叠屏', 'AR', 'VR', 'MR', 'XR', '苹果产业链',
                   '汽车产业链', '新能源车', '自动驾驶', '智能座舱', '车路云',
                   '医药', '医疗', '器械', 'CRO', 'CXO', '创新药', '仿制药',
                   '白酒', '食品饮料', '调味品', '乳制品', '啤酒', '黄酒',
                   '银行', '券商', '保险', '多元金融', '资管', '信托',
                   '地产', '建筑', '基建', '水利', '公路', '铁路', '机场',
                   '化工', '材料', '稀土', '磁材', '钛白粉', '氟化工',
                   '有色', '黄金', '铜', '铝', '锌', '锂', '钴', '镍',
                   '农业', '种业', '化肥', '农药', '饲料', '养殖',
                   '电力', '煤炭', '油气', '核电', '水电', '风电', '光伏',
                   '通信', '传媒', '游戏', '影视', '广告', '营销',
                   '计算机', '软件', '信息安全', '国产化', '信创', '鸿蒙',
                   '家电', '轻工', '纺织', '服装', '家居', '建材',
                   '机械', '设备', '工程机械', '机床', '机器人', '自动化',
                   '电子', '元器件', 'PCB', 'MLCC', '射频', '光模块',
                   '医药商业', '医疗服务', 'CRO', 'CXO', 'CDMO', '创新药',
        ]
        for concept in concepts:
            for match in re.finditer(re.escape(concept), text, re.IGNORECASE):
                entities.append({'type': 'concept', 'value': concept, 'span': match.span()})
        
        # 去重
        seen = set()
        unique = []
        for e in entities:
            key = (e['span'][0], e['span'][1])
            if key not in seen:
                seen.add(key)
                unique.append(e)
        
        return unique

# ============== 情感分析器 ==============

class LexiconSentimentAnalyzer:
    """基于情感词典的金融情感分析器"""
    
    def __init__(self):
        self.pos_words = FINANCE_POSITIVE
        self.neg_words = FINANCE_NEGATIVE
        self.negation_words = NEGATION_WORDS
        self.degree_words = DEGREE_WORDS
        self.transition_words = TRANSITION_WORDS
        self.processor = TextProcessor()
    
    def analyze(self, text: str) -> SentimentResult:
        if not text or not text.strip():
            return SentimentResult(
                text=text, score=0.0, label='NEUTRAL', confidence=0.0,
                model='lexicon', timestamp=datetime.utcnow().isoformat(),
                entities=[], keywords=[],
            )
        
        clean_text = self.processor.clean(text)
        words = self.processor.segment(clean_text)
        entities = self.processor.extract_finance_entities(text)
        keywords = self.processor.extract_keywords(clean_text, top_k=10)
        
        score = 0.0
        weight = 1.0
        negation = False
        transition_boost = 1.0
        
        for word in words:
            if word in self.transition_words:
                transition_boost = 1.5
                negation = False
                weight = 1.0
                continue
            
            if word in self.negation_words:
                negation = not negation
                continue
            
            if word in self.degree_words:
                weight *= self.degree_words[word]
                continue
            
            if word in self.pos_words:
                w = weight * transition_boost * (1 if not negation else -1)
                score += w
                weight = 1.0
                negation = False
                transition_boost = 1.0
                continue
            
            if word in self.neg_words:
                w = -weight * transition_boost * (1 if not negation else -1)
                score += w
                weight = 1.0
                negation = False
                transition_boost = 1.0
                continue
        
        word_count = len(words)
        if word_count > 0:
            normalized = max(-1.0, min(1.0, score / (word_count * 0.3 + 1)))
        else:
            normalized = 0.0
        
        if normalized > 0.5:
            label = 'VERY_POSITIVE'
        elif normalized > 0.1:
            label = 'POSITIVE'
        elif normalized < -0.5:
            label = 'VERY_NEGATIVE'
        elif normalized < -0.1:
            label = 'NEGATIVE'
        else:
            label = 'NEUTRAL'
        
        sentiment_hits = sum(1 for w in words if w in self.pos_words or w in self.neg_words)
        confidence = min(0.9, abs(normalized) + 0.1 + sentiment_hits * 0.05)
        
        return SentimentResult(
            text=text, score=round(normalized, 4), label=label,
            confidence=round(confidence, 4), model='lexicon',
            timestamp=datetime.utcnow().isoformat(),
            entities=entities, keywords=keywords,
        )
    
    def batch_analyze(self, texts: List[str]) -> List[SentimentResult]:
        return [self.analyze(text) for text in texts]
    
    def analyze_file(self, file_path: str, text_field: str = 'content') -> List[Dict]:
        results = []
        path = Path(file_path)
        
        if path.suffix == '.jsonl':
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        text = data.get(text_field, '')
                        if text:
                            result = self.analyze(text)
                            result_dict = result.to_dict()
                            result_dict['source_data'] = data
                            results.append(result_dict)
        elif path.suffix == '.json':
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        text = item.get(text_field, '')
                        if text:
                            result = self.analyze(text)
                            result_dict = result.to_dict()
                            result_dict['source_data'] = item
                            results.append(result_dict)
                elif isinstance(data, dict):
                    text = data.get(text_field, '')
                    if text:
                        result = self.analyze(text)
                        result_dict = result.to_dict()
                        result_dict['source_data'] = data
                        results.append(result_dict)
        
        return results


# ============== 股票舆情聚合 ==============

class StockSentimentAggregator:
    """股票舆情聚合分析"""
    
    def __init__(self):
        self.analyzer = LexiconSentimentAnalyzer()
    
    def analyze_stock_posts(self, posts: List[Dict], text_field: str = 'content') -> Dict:
        if not posts:
            return {}
        
        results = []
        for post in posts:
            text = post.get(text_field, '')
            if not text:
                continue
            result = self.analyzer.analyze(text)
            result_dict = result.to_dict()
            result_dict['post_meta'] = {k: v for k, v in post.items() if k != text_field}
            results.append(result_dict)
        
        if not results:
            return {}
        
        scores = [r['score'] for r in results]
        labels = [r['label'] for r in results]
        label_counts = Counter(labels)
        total = len(results)
        
        weighted_score = 0
        total_weight = 0
        for r in results:
            meta = r.get('post_meta', {})
            weight = 1
            if 'read_count' in meta:
                weight += min(meta['read_count'] / 10000, 5)
            if 'comment_count' in meta:
                weight += min(meta['comment_count'] / 1000, 3)
            weighted_score += r['score'] * weight
            total_weight += weight
        
        avg_score = sum(scores) / total if total > 0 else 0
        weighted_avg = weighted_score / total_weight if total_weight > 0 else 0
        
        all_keywords = []
        for r in results:
            all_keywords.extend(r.get('keywords', []))
        top_keywords = Counter(all_keywords).most_common(20)
        
        all_entities = []
        for r in results:
            all_entities.extend(r.get('entities', []))
        entity_counts = Counter([f"{e['type']}:{e.get('code', e.get('value', ''))}" for e in all_entities])
        
        return {
            'post_count': total,
            'avg_score': round(avg_score, 4),
            'weighted_avg_score': round(weighted_avg, 4),
            'label_distribution': dict(label_counts),
            'positive_ratio': round((label_counts.get('POSITIVE', 0) + label_counts.get('VERY_POSITIVE', 0)) / total, 4) if total > 0 else 0,
            'negative_ratio': round((label_counts.get('NEGATIVE', 0) + label_counts.get('VERY_NEGATIVE', 0)) / total, 4) if total > 0 else 0,
            'neutral_ratio': round(label_counts.get('NEUTRAL', 0) / total, 4) if total > 0 else 0,
            'top_keywords': top_keywords,
            'top_entities': entity_counts.most_common(10),
            'detail_results': results,
            'timestamp': datetime.utcnow().isoformat(),
        }
    
    def sentiment_signal(self, agg_result: Dict) -> Dict:
        if not agg_result:
            return {'signal': 'NEUTRAL', 'strength': 0, 'reason': '无数据'}
        
        score = agg_result.get('weighted_avg_score', agg_result.get('avg_score', 0))
        pos_ratio = agg_result.get('positive_ratio', 0)
        neg_ratio = agg_result.get('negative_ratio', 0)
        count = agg_result.get('post_count', 0)
        
        if count < 5:
            return {'signal': 'NEUTRAL', 'strength': 0, 'reason': f'样本量不足({count}条)'}
        
        if score > 0.3 and pos_ratio > 0.6:
            return {'signal': 'BULLISH', 'strength': min(abs(score) * 2, 1), 'reason': f'舆情强烈看多(score={score:.2f}, pos={pos_ratio:.1%})'}
        elif score > 0.1 and pos_ratio > 0.5:
            return {'signal': 'WEAK_BULLISH', 'strength': min(abs(score), 0.5), 'reason': f'舆情偏多(score={score:.2f}, pos={pos_ratio:.1%})'}
        elif score < -0.3 and neg_ratio > 0.6:
            return {'signal': 'BEARISH', 'strength': min(abs(score) * 2, 1), 'reason': f'舆情强烈看空(score={score:.2f}, neg={neg_ratio:.1%})'}
        elif score < -0.1 and neg_ratio > 0.5:
            return {'signal': 'WEAK_BEARISH', 'strength': min(abs(score), 0.5), 'reason': f'舆情偏空(score={score:.2f}, neg={neg_ratio:.1%})'}
        else:
            return {'signal': 'NEUTRAL', 'strength': 0, 'reason': f'舆情中性(score={score:.2f}, pos={pos_ratio:.1%}, neg={neg_ratio:.1%})'}


# ============== 主程序 ==============

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='金融舆情分析工具')
    parser.add_argument('input', nargs='?', help='输入文本或文件路径')
    parser.add_argument('--file', '-f', help='输入文件路径 (JSON/JSONL)')
    parser.add_argument('--field', default='content', help='文本字段名')
    parser.add_argument('--output', '-o', help='输出文件路径')
    parser.add_argument('--batch', action='store_true', help='批量处理模式')
    
    args = parser.parse_args()
    
    analyzer = LexiconSentimentAnalyzer()
    
    if args.file:
        results = analyzer.analyze_file(args.file, args.field)
        output_data = {
            'summary': {
                'total': len(results),
                'avg_score': sum(r['score'] for r in results) / len(results) if results else 0,
                'label_dist': dict(Counter(r['label'] for r in results)),
            },
            'results': results,
        }
    elif args.input:
        result = analyzer.analyze(args.input)
        output_data = result.to_dict()
    else:
        demo_texts = [
            "人民网今天大涨5%，业绩超预期，机构纷纷买入，北向资金大幅流入，看好后市！",
            "浦发银行业绩暴雷，不良率飙升，大股东违规担保，面临退市风险，坚决卖出！",
            "茅台估值合理，分红慷慨，长期持有，短期波动不用管，价值投资首选。",
            "东方财富今天跌停，量能萎缩，技术面破位，主力资金大幅流出，短期避险。",
            "恒瑞医药创新药管线丰富，PD-1获批在即，业绩高增长确定性强，机构重仓。",
        ]
        
        print("=== 演示模式 ===\n")
        for i, text in enumerate(demo_texts, 1):
            result = analyzer.analyze(text)
            print(f"[{i}] {text}")
            print(f"    情感: {result.label} (score={result.score:.3f}, conf={result.confidence:.3f})")
            print(f"    实体: {result.entities}")
            print(f"    关键词: {result.keywords}")
            print()
        
        aggregator = StockSentimentAggregator()
        posts = [{'content': t, 'read_count': 1000, 'comment_count': 50} for t in demo_texts]
        agg = aggregator.analyze_stock_posts(posts)
        signal = aggregator.sentiment_signal(agg)
        
        print("=== 聚合分析 ===")
        print(f"帖子数: {agg['post_count']}")
        print(f"平均分: {agg['avg_score']:.3f}")
        print(f"加权平均分: {agg['weighted_avg_score']:.3f}")
        print(f"正面比例: {agg['positive_ratio']:.1%}")
        print(f"负面比例: {agg['negative_ratio']:.1%}")
        print(f"中性比例: {agg['neutral_ratio']:.1%}")
        print(f"热门关键词: {agg['top_keywords'][:10]}")
        print(f"信号: {signal['signal']} (强度: {signal['strength']:.2f}) - {signal['reason']}")
        
        output_data = {'demo': True, 'aggregation': agg, 'signal': signal}
    
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}")
    else:
        print(json.dumps(output_data, ensure_ascii=False, indent=2))

# ============== 统一接口 ==============

def analyze_sentiment(text: str):
    """统一接口：分析单条文本情感"""
    analyzer = LexiconSentimentAnalyzer()
    result = analyzer.analyze(text)
    from ..core import FinanceData
    return FinanceData(
        source='lexicon',
        data_type='sentiment',
        symbol='',
        timestamp=datetime.utcnow().isoformat(),
        payload=result.to_dict(),
        meta={'model': 'lexicon'}
    )

def analyze_stock_sentiment(posts: List[Dict], symbol: str = '') -> 'FinanceData':
    """统一接口：分析股票舆情聚合"""
    aggregator = StockSentimentAggregator()
    agg = aggregator.analyze_stock_posts(posts)
    signal = aggregator.sentiment_signal(agg)
    payload = {**agg, 'signal': signal}
    from ..core import FinanceData
    return FinanceData(
        source='lexicon',
        data_type='sentiment_agg',
        symbol=symbol,
        timestamp=datetime.utcnow().isoformat(),
        payload=payload,
        meta={'model': 'lexicon'}
    )


if __name__ == '__main__':
    main()