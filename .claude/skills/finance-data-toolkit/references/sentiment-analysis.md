# 舆情分析全流程模块

覆盖：文本预处理、情感极性判断、实体识别、热度追踪、异动预警、多源交叉验证。

## 1. 核心架构

```python
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict
from enum import Enum

class SentimentLabel(Enum):
    VERY_NEGATIVE = -2
    NEGATIVE = -1
    NEUTRAL = 0
    POSITIVE = 1
    VERY_POSITIVE = 2

@dataclass
class SentimentResult:
    text: str
    score: float                    # [-1, 1] 连续值
    label: SentimentLabel           # 离散标签
    confidence: float               # [0, 1] 置信度
    aspects: Dict[str, float] = None  # 方面级情感: {'业绩': 0.8, '估值': -0.3}
    entities: List[Dict] = None     # 实体: [{type: 'stock', name: '平安银行', code: '000001.SZ', sentiment: 0.5}]
    keywords: List[str] = None      # 关键词
    model: str = ""                 # 使用的模型名
    timestamp: datetime = None
```

## 2. 文本预处理管道

```python
import re
import jieba
import jieba.posseg as pseg
from typing import List

class TextPreprocessor:
    """金融文本预处理"""
    
    def __init__(self, custom_dict_path: str = None):
        if custom_dict_path:
            jieba.load_userdict(custom_dict_path)
        # 添加金融术语
        self._add_finance_terms()
    
    def _add_finance_terms(self):
        terms = [
            '涨停', '跌停', '打板', '炸板', '封板',
            '主力', '游资', '北向', '融券', '融资',
            '业绩暴雷', '业绩预增', '业绩预减',
            '增持', '减持', '回购', '分红', '送转',
            '利好', '利空', '题材', '概念', '龙头',
            '突破', '支撑', '压力', '均线', 'MACD',
        ]
        for t in terms:
            jieba.add_word(t)
    
    def clean(self, text: str) -> str:
        """清洗：去除URL、@用户、特殊符号、多余空白"""
        # 去除 URL
        text = re.sub(r'https?://\S+', '', text)
        # 去除 @用户
        text = re.sub(r'@\w+', '', text)
        # 去除股票代码格式 (保留代码用于实体识别)
        # text = re.sub(r'\b\d{6}\.(SZ|SH|BJ)\b', '', text)
        # 去除多余空白
        text = re.sub(r'\s+', ' ', text)
        # 去除表情符号 (可选保留)
        text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]', '', text)
        return text.strip()
    
    def segment(self, text: str, with_pos: bool = False) -> List[str]:
        """分词"""
        if with_pos:
            return [(w.word, w.flag) for w in pseg.cut(text)]
        return list(jieba.cut(text))
    
    def extract_keywords(self, text: str, top_k: int = 10,
                          with_weight: bool = False) -> List:
        """TF-IDF / TextRank 关键词提取"""
        import jieba.analyse
        return jieba.analyse.extract_tags(text, topK=top_k, withWeight=with_weight)
    
    def extract_finance_entities(self, text: str) -> List[Dict]:
        """金融实体识别 (规则 + 词典 + NER)"""
        entities = []
        
        # 股票代码
        for match in re.finditer(r'\b(\d{6})\.(SZ|SH|BJ)\b', text):
            entities.append({
                'type': 'stock',
                'code': f"{match.group(1)}.{match.group(2)}",
                'name': '',  # 需映射表
                'span': match.span(),
            })
        
        # 金额/比例
        for match in re.finditer(r'(\d+(?:\.\d+)?)\s*(亿|万|万元|亿元|%)', text):
            entities.append({
                'type': 'amount',
                'value': match.group(1),
                'unit': match.group(2),
                'span': match.span(),
            })
        
        # 时间
        for match in re.finditer(r'(\d{4}年\d{1,2}月|\d{1,2}月\d{1,2}日|Q[1-4]|半年报|年报|季报)', text):
            entities.append({
                'type': 'time',
                'value': match.group(1),
                'span': match.span(),
            })
        
        return entities
```

## 3. 情感分析模型

### 3.1 规则/词典法 (轻量级)

```python
class LexiconSentimentAnalyzer:
    """基于情感词典的轻量级分析"""
    
    def __init__(self, dict_path: str = None):
        self.pos_words = set()
        self.neg_words = set()
        self.negation_words = {'不', '没', '无', '非', '未', '别', '莫', '勿', '休', '免'}
        self.degree_words = {
            '极其': 2.0, '非常': 1.8, '特别': 1.7, '十分': 1.6, '很': 1.3,
            '比较': 1.2, '较': 1.1, '有点': 0.8, '略微': 0.6, '稍微': 0.5,
        }
        self._load_dict(dict_path)
    
    def _load_dict(self, path: str):
        # 加载正面/负面词典
        # 可用：知乎情感词典、NTUSD、大连理工情感词典、金融领域自建词典
        pass
    
    def analyze(self, text: str) -> SentimentResult:
        words = list(jieba.cut(text))
        score = 0
        weight = 1.0
        negation = False
        
        for i, word in enumerate(words):
            if word in self.negation_words:
                negation = not negation
                continue
            
            if word in self.degree_words:
                weight *= self.degree_words[word]
                continue
            
            if word in self.pos_words:
                w = weight * (1 if not negation else -1)
                score += w
                weight = 1.0
                negation = False
            elif word in self.neg_words:
                w = -weight * (1 if not negation else -1)
                score += w
                weight = 1.0
                negation = False
        
        # 归一化到 [-1, 1]
        normalized = max(-1, min(1, score / (len(words) * 0.5 + 1)))
        
        return SentimentResult(
            text=text,
            score=normalized,
            label=self._score_to_label(normalized),
            confidence=min(0.8, abs(normalized) + 0.2),
            model='lexicon',
            timestamp=datetime.utcnow(),
        )
    
    def _score_to_label(self, score: float) -> SentimentLabel:
        if score > 0.5: return SentimentLabel.VERY_POSITIVE
        if score > 0.1: return SentimentLabel.POSITIVE
        if score < -0.5: return SentimentLabel.VERY_NEGATIVE
        if score < -0.1: return SentimentLabel.NEGATIVE
        return SentimentLabel.NEUTRAL
```

### 3.2 机器学习模型 (TF-IDF + 逻辑回归 / SVM)

```python
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
import joblib

class MLSentimentAnalyzer:
    """传统 ML 情感分类器"""
    
    def __init__(self, model_path: str = None):
        if model_path:
            self.pipeline = joblib.load(model_path)
        else:
            self.pipeline = Pipeline([
                ('tfidf', TfidfVectorizer(
                    max_features=10000,
                    ngram_range=(1, 2),
                    token_pattern=r'(?u)\b\w+\b',
                )),
                ('clf', LogisticRegression(
                    C=1.0,
                    class_weight='balanced',
                    max_iter=1000,
                    random_state=42,
                )),
            ])
    
    def train(self, texts: List[str], labels: List[int]):
        """labels: -1(负面), 0(中性), 1(正面)"""
        self.pipeline.fit(texts, labels)
    
    def predict(self, text: str) -> SentimentResult:
        proba = self.pipeline.predict_proba([text])[0]
        classes = self.pipeline.classes_
        pred_idx = proba.argmax()
        label = classes[pred_idx]
        confidence = proba[pred_idx]
        
        # 映射到连续分数
        score_map = {-1: -0.8, 0: 0.0, 1: 0.8}
        score = score_map.get(label, 0.0)
        
        return SentimentResult(
            text=text,
            score=score,
            label=SentimentLabel(label),
            confidence=confidence,
            model='ml_logistic',
            timestamp=datetime.utcnow(),
        )
    
    def save(self, path: str):
        joblib.dump(self.pipeline, path)
```

### 3.3 深度学习模型 (BERT / FinBERT / RoBERTa)

```python
# 使用 transformers 库
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

class BertSentimentAnalyzer:
    """基于 BERT 的金融情感分析"""
    
    def __init__(self, model_name: str = 'ProsusAI/finbert',
                 device: str = 'cuda' if torch.cuda.is_available() else 'cpu'):
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name).to(device)
        self.model.eval()
        
        # FinBERT 标签: 0=Positive, 1=Negative, 2=Neutral
        self.label_map = {0: 1, 1: -1, 2: 0}
    
    def analyze(self, text: str, max_length: int = 512) -> SentimentResult:
        inputs = self.tokenizer(
            text,
            return_tensors='pt',
            truncation=True,
            max_length=max_length,
            padding=True,
        ).to(self.device)
        
        with torch.no_grad():
            outputs = self.model(**inputs)
            probs = torch.softmax(outputs.logits, dim=-1).cpu().numpy()[0]
        
        pred_idx = probs.argmax()
        label = self.label_map[pred_idx]
        confidence = probs[pred_idx]
        
        # 连续分数映射
        score_map = {1: 0.8, -1: -0.8, 0: 0.0}
        score = score_map[label]
        
        # 可选：用概率分布计算期望分数
        expected_score = sum(self.label_map[i] * probs[i] for i in range(3))
        
        return SentimentResult(
            text=text,
            score=expected_score,
            label=SentimentLabel(label),
            confidence=float(confidence),
            model='finbert',
            timestamp=datetime.utcnow(),
        )
    
    def batch_analyze(self, texts: List[str], batch_size: int = 32) -> List[SentimentResult]:
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            inputs = self.tokenizer(
                batch,
                return_tensors='pt',
                truncation=True,
                max_length=512,
                padding=True,
            ).to(self.device)
            
            with torch.no_grad():
                outputs = self.model(**inputs)
                probs = torch.softmax(outputs.logits, dim=-1).cpu().numpy()
            
            for j, prob in enumerate(probs):
                pred_idx = prob.argmax()
                label = self.label_map[pred_idx]
                confidence = prob[pred_idx]
                expected_score = sum(self.label_map[k] * prob[k] for k in range(3))
                
                results.append(SentimentResult(
                    text=batch[j],
                    score=expected_score,
                    label=SentimentLabel(label),
                    confidence=float(confidence),
                    model='finbert',
                    timestamp=datetime.utcnow(),
                ))
        return results
```

### 3.4 方面级情感分析 (ABSA)

```python
class AspectSentimentAnalyzer:
    """方面级情感：针对特定实体/属性的情感"""
    
    ASPECTS = [
        '业绩', '营收', '利润', '毛利率', '净利率',
        '估值', 'PE', 'PB', '股价', '市值',
        '分红', '送转', '回购', '增持', '减持',
        '行业', '政策', '竞争', '产能', '订单',
        '管理层', '治理', '研发', '新产品', '扩产',
    ]
    
    def __init__(self, base_analyzer):
        self.base = base_analyzer  # 任意上述分析器
    
    def analyze(self, text: str) -> Dict[str, float]:
        """返回各方面的情感分数"""
        # 简化：句子级切分 + 方面关键词匹配
        sentences = self._split_sentences(text)
        aspect_scores = {a: [] for a in self.ASPECTS}
        
        for sent in sentences:
            sent_result = self.base.analyze(sent)
            for aspect in self.ASPECTS:
                if aspect in sent:
                    aspect_scores[aspect].append(sent_result.score)
        
        # 平均
        return {
            a: sum(scores)/len(scores) 
            for a, scores in aspect_scores.items() if scores
        }
    
    def _split_sentences(self, text: str) -> List[str]:
        import re
        return [s.strip() for s in re.split(r'[。！？\n]', text) if s.strip()]
```

## 4. 实体识别与链接

```python
class FinanceEntityRecognizer:
    """金融实体识别 + 链接到标准代码"""
    
    def __init__(self, stock_dict_path: str = None):
        self.stock_name_to_code = self._load_stock_dict(stock_dict_path)
        self.code_to_name = {v: k for k, v in self.stock_name_to_code.items()}
    
    def _load_stock_dict(self, path: str) -> Dict[str, str]:
        """加载股票名称->代码映射"""
        # 从 AKShare / Tushare 获取全量股票列表
        import akshare as ak
        df = ak.stock_zh_a_spot_em()[['代码', '名称']]
        return dict(zip(df['名称'], df['代码']))
    
    def recognize(self, text: str) -> List[Dict]:
        entities = []
        
        # 1. 代码直接匹配
        import re
        for match in re.finditer(r'\b(\d{6})\.(SZ|SH|BJ)\b', text):
            code = f"{match.group(1)}.{match.group(2)}"
            entities.append({
                'type': 'stock',
                'code': code,
                'name': self.code_to_name.get(code, ''),
                'span': match.span(),
                'confidence': 1.0,
            })
        
        # 2. 名称匹配 (最长匹配)
        for name, code in self.stock_name_to_code.items():
            if name in text and len(name) >= 2:
                # 找到所有出现位置
                start = 0
                while True:
                    idx = text.find(name, start)
                    if idx == -1:
                        break
                    entities.append({
                        'type': 'stock',
                        'code': code,
                        'name': name,
                        'span': (idx, idx + len(name)),
                        'confidence': 0.9,
                    })
                    start = idx + 1
        
        # 3. 去重 (同一位置保留置信度高的)
        entities = self._deduplicate_entities(entities)
        
        # 4. 其他实体类型
        entities.extend(self._extract_other_entities(text))
        
        return entities
    
    def _deduplicate_entities(self, entities: List[Dict]) -> List[Dict]:
        """按 span 去重"""
        span_map = {}
        for e in entities:
            span = e['span']
            if span not in span_map or e['confidence'] > span_map[span]['confidence']:
                span_map[span] = e
        return list(span_map.values())
    
    def _extract_other_entities(self, text: str) -> List[Dict]:
        entities = []
        import re
        
        # 金额
        for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(亿|万|万元|亿元|百万|千万|%)', text):
            entities.append({'type': 'amount', 'value': m.group(1), 'unit': m.group(2), 'span': m.span()})
        
        # 机构
        org_keywords = ['基金', '券商', '银行', '保险', '信托', '私募', '公募', 'QFII', '北向资金']
        for kw in org_keywords:
            if kw in text:
                idx = text.find(kw)
                entities.append({'type': 'institution', 'name': kw, 'span': (idx, idx+len(kw))})
        
        # 人名 (简化：董事长、总经理、分析师 + 名字模式)
        for m in re.finditer(r'(董事长|总经理|CEO|CFO|分析师|研究员)\s*[\u4e00-\u9fa5]{2,4}', text):
            entities.append({'type': 'person', 'role': m.group(1), 'name': m.group(0), 'span': m.span()})
        
        return entities
```

## 5. 热度追踪与异动预警

```python
class HeatTracker:
    """舆情热度追踪与异动检测"""
    
    def __init__(self, window_hours: int = 24):
        self.window = pd.Timedelta(hours=window_hours)
    
    def compute_heat_index(self, 
                          news_data: pd.DataFrame,
                          guba_data: pd.DataFrame,
                          wechat_data: pd.DataFrame = None) -> pd.DataFrame:
        """计算多源热度指数"""
        now = pd.Timestamp.utcnow()
        cutoff = now - self.window
        
        # 新闻热度: 文章数 * 来源权重
        news_recent = news_data[news_data['publish_time'] > cutoff]
        news_heat = news_recent.groupby('symbol').apply(
            lambda x: sum(x['source_weight'] * x['importance'])
        ).rename('news_heat')
        
        # 股吧热度: 阅读数 + 评论数 + 情感极化度
        guba_recent = guba_data[guba_data['publish_time'] > cutoff]
        guba_heat = guba_recent.groupby('symbol').apply(
            lambda x: (x['read_count'].sum() * 0.001 + 
                      x['comment_count'].sum() * 0.01 + 
                      x['sentiment'].abs().mean() * 100)
        ).rename('guba_heat')
        
        # 微信热度 (如有)
        wechat_heat = pd.Series(dtype=float)
        if wechat_data is not None:
            wechat_recent = wechat_data[wechat_data['publish_time'] > cutoff]
            wechat_heat = wechat_recent.groupby('symbol').size().rename('wechat_heat')
        
        # 合并
        heat = pd.concat([news_heat, guba_heat, wechat_heat], axis=1).fillna(0)
        heat['total_heat'] = heat.sum(axis=1)
        heat['heat_rank'] = heat['total_heat'].rank(ascending=False, method='dense')
        
        return heat.sort_values('total_heat', ascending=False)
    
    def detect_anomaly(self, 
                      heat_history: pd.DataFrame,
                      zscore_threshold: float = 3.0) -> pd.DataFrame:
        """检测热度异动 (Z-score / 变化率)"""
        # heat_history: index=timestamp, columns=symbol, values=heat_index
        
        # 滚动均值/标准差
        rolling_mean = heat_history.rolling('24H').mean()
        rolling_std = heat_history.rolling('24H').std()
        
        # 当前值
        current = heat_history.iloc[-1]
        
        # Z-score
        zscore = (current - rolling_mean.iloc[-1]) / (rolling_std.iloc[-1] + 1e-6)
        
        # 环比变化率
        prev = heat_history.iloc[-2] if len(heat_history) > 1 else current
        pct_change = (current - prev) / (prev + 1e-6)
        
        anomalies = pd.DataFrame({
            'current_heat': current,
            'zscore': zscore,
            'pct_change': pct_change,
            'is_anomaly': (zscore.abs() > zscore_threshold) | (pct_change > 5.0),
        })
        
        return anomalies[anomalies['is_anomaly']].sort_values('zscore', key=abs, ascending=False)
    
    def alert_rules(self, anomalies: pd.DataFrame, 
                   sentiment_data: pd.DataFrame = None) -> List[Dict]:
        """生成预警规则"""
        alerts = []
        for _, row in anomalies.iterrows():
            symbol = row.name
            alert = {
                'symbol': symbol,
                'alert_type': 'heat_surge',
                'heat_index': row['current_heat'],
                'zscore': row['zscore'],
                'pct_change': row['pct_change'],
                'timestamp': pd.Timestamp.utcnow(),
            }
            
            # 结合情感
            if sentiment_data is not None and symbol in sentiment_data.index:
                sent = sentiment_data.loc[symbol]
                alert['sentiment'] = sent['score']
                alert['sentiment_label'] = sent['label']
                
                if sent['score'] < -0.5:
                    alert['alert_type'] = 'negative_heat_surge'
                    alert['risk_level'] = 'high'
                elif sent['score'] > 0.5:
                    alert['alert_type'] = 'positive_heat_surge'
                    alert['risk_level'] = 'medium'
            
            alerts.append(alert)
        return alerts
```

## 6. 多源交叉验证

```python
class CrossValidator:
    """多源情感一致性验证"""
    
    def __init__(self):
        self.source_weights = {
            'news': 0.4,
            'guba': 0.3,
            'wechat': 0.2,
            'research_report': 0.1,
        }
    
    def validate(self, 
                symbol: str,
                news_sentiment: float = None,
                guba_sentiment: float = None,
                wechat_sentiment: float = None,
                report_sentiment: float = None) -> Dict:
        """计算加权共识情感"""
        sentiments = {}
        weights = {}
        
        if news_sentiment is not None:
            sentiments['news'] = news_sentiment
            weights['news'] = self.source_weights['news']
        if guba_sentiment is not None:
            sentiments['guba'] = guba_sentiment
            weights['guba'] = self.source_weights['guba']
        if wechat_sentiment is not None:
            sentiments['wechat'] = wechat_sentiment
            weights['wechat'] = self.source_weights['wechat']
        if report_sentiment is not None:
            sentiments['report'] = report_sentiment
            weights['report'] = self.source_weights['report']
        
        if not sentiments:
            return {'consensus': 0, 'agreement': 0, 'sources': {}}
        
        # 归一化权重
        total_w = sum(weights.values())
        weights = {k: v/total_w for k, v in weights.items()}
        
        # 加权共识
        consensus = sum(sentiments[k] * weights[k] for k in sentiments)
        
        # 一致性度量: 方差越小越一致
        values = list(sentiments.values())
        agreement = 1 - np.std(values) / (np.mean(np.abs(values)) + 1e-6)
        agreement = max(0, min(1, agreement))
        
        # 分歧检测
        divergence = max(values) - min(values)
        
        return {
            'consensus': consensus,
            'agreement': agreement,
            'divergence': divergence,
            'sources': sentiments,
            'weights': weights,
            'signal': 'bullish' if consensus > 0.3 else ('bearish' if consensus < -0.3 else 'neutral'),
        }
```

## 7. 完整流水线示例

```python
class SentimentPipeline:
    """端到端舆情分析流水线"""
    
    def __init__(self):
        self.preprocessor = TextPreprocessor()
        self.lexicon = LexiconSentimentAnalyzer()
        self.ml = MLSentimentAnalyzer('models/sentiment_lr.pkl')
        self.bert = BertSentimentAnalyzer()  # 可选，GPU 环境
        self.entity_recognizer = FinanceEntityRecognizer()
        self.aspect_analyzer = AspectSentimentAnalyzer(self.bert)
        self.heat_tracker = HeatTracker()
        self.cross_validator = CrossValidator()
    
    def analyze_batch(self, 
                     news_list: List[FinanceNews],
                     guba_list: List[GubaPost] = None) -> Dict:
        """批量分析"""
        results = {
            'news': [],
            'guba': [],
            'symbol_summary': {},
        }
        
        # 1. 新闻分析
        for news in news_list:
            clean_text = self.preprocessor.clean(news.content)
            
            # 多模型集成
            lexicon_res = self.lexicon.analyze(clean_text)
            ml_res = self.ml.predict(clean_text)
            bert_res = self.bert.analyze(clean_text) if hasattr(self, 'bert') else None
            
            # 实体识别
            entities = self.entity_recognizer.recognize(clean_text)
            
            # 方面级情感
            aspects = self.aspect_analyzer.analyze(clean_text)
            
            # 融合结果 (加权平均)
            final_score = self._ensemble_score([lexicon_res, ml_res, bert_res])
            
            results['news'].append({
                'news_id': news.news_id,
                'symbol': news.symbols,
                'score': final_score,
                'label': self._score_to_label(final_score),
                'entities': entities,
                'aspects': aspects,
                'models': {'lexicon': lexicon_res.score, 'ml': ml_res.score, 'bert': bert_res.score if bert_res else None},
            })
        
        # 2. 股吧分析 (类似)
        # ...
        
        # 3. 按标的汇总
        results['symbol_summary'] = self._aggregate_by_symbol(results)
        
        # 4. 热度追踪
        heat = self.heat_tracker.compute_heat_index(...)
        anomalies = self.heat_tracker.detect_anomaly(heat)
        alerts = self.heat_tracker.alert_rules(anomalies)
        
        results['heat'] = heat
        results['alerts'] = alerts
        
        return results
    
    def _ensemble_score(self, model_results: List) -> float:
        weights = {'lexicon': 0.2, 'ml': 0.3, 'bert': 0.5}
        scores = []
        for res in model_results:
            if res:
                scores.append(res.score * weights.get(res.model, 0.1))
        return sum(scores) / sum(weights.values()) if scores else 0
    
    def _score_to_label(self, score: float) -> str:
        if score > 0.3: return 'positive'
        if score < -0.3: return 'negative'
        return 'neutral'
    
    def _aggregate_by_symbol(self, results: Dict) -> pd.DataFrame:
        """按股票代码聚合"""
        all_items = []
        for item in results['news']:
            for sym in item['symbol']:
                all_items.append({'symbol': sym, 'score': item['score'], 'source': 'news'})
        # ... guba 同理
        
        df = pd.DataFrame(all_items)
        if df.empty:
            return pd.DataFrame()
        
        return df.groupby('symbol').agg(
            avg_score=('score', 'mean'),
            std_score=('score', 'std'),
            count=('score', 'count'),
            sources=('source', lambda x: list(x.unique())),
        ).sort_values('avg_score', ascending=False)
```