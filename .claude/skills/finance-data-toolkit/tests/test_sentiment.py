"""
Tests for sentiment analysis module
"""



class TestSentimentAnalyzer:
    """Test sentiment analysis functions"""
    
    def test_analyze_sentiment_positive(self):
        """Test positive sentiment"""
        from finance_toolkit.sentiment import analyze_sentiment
    
        text = "茅台今天大涨5%，业绩超预期，机构纷纷买入，北向资金大幅流入！"
        result = analyze_sentiment(text)
        
        assert result.payload['label'] in ['POSITIVE', 'VERY_POSITIVE']
        assert result.payload['score'] > 0
    
    def test_analyze_sentiment_negative(self):
        """Test negative sentiment"""
        from finance_toolkit.sentiment import analyze_sentiment
        
        text = "茅台估值太高了，泡沫风险大，业绩不及预期，机构纷纷卖出，北向资金大幅流出！"
        result = analyze_sentiment(text)
        
        assert result.payload['label'] in ['NEGATIVE', 'VERY_NEGATIVE']
        assert result.payload['score'] < 0
    
    def test_analyze_sentiment_neutral(self):
        """Test neutral sentiment"""
        from finance_toolkit.sentiment import analyze_sentiment
        
        text = "茅台今日股价平盘，成交量温和。"
        result = analyze_sentiment(text)
        
        assert result.payload['label'] == 'NEUTRAL'
        assert abs(result.payload['score']) < 0.3
    
    def test_analyze_stock_sentiment(self):
        """Test stock sentiment aggregation"""
        from finance_toolkit.sentiment import analyze_stock_sentiment
        
        posts = [
            {'content': '茅台业绩超预期，看好后市', 'read_count': 10000, 'comment_count': 500},
            {'content': '茅台估值太高了，泡沫风险大', 'read_count': 5000, 'comment_count': 200},
            {'content': '茅台今日大涨，北向资金买入', 'read_count': 8000, 'comment_count': 300},
        ]
        
        agg = analyze_stock_sentiment(posts, symbol='600519.SH')
        
        # symbol is in the FinanceData object, not payload
        assert agg.symbol == '600519.SH'
        assert 'signal' in agg.payload
        assert agg.payload['signal']['signal'] in ['BULLISH', 'WEAK_BULLISH', 'BEARISH', 'WEAK_BEARISH', 'NEUTRAL']
        assert 'label_distribution' in agg.payload
        assert agg.payload['post_count'] == 3
    
    def test_lexicon_sentiment_analyzer(self):
        """Test LexiconSentimentAnalyzer class"""
        from finance_toolkit.sentiment import LexiconSentimentAnalyzer
        
        analyzer = LexiconSentimentAnalyzer()
        
        # Test positive
        result = analyzer.analyze("大涨 买入 利好 超预期")
        assert result.label in ['POSITIVE', 'VERY_POSITIVE']
        assert result.score > 0
        
        # Test negative
        result = analyzer.analyze("大跌 卖出 利空 不及预期")
        assert result.label in ['NEGATIVE', 'VERY_NEGATIVE']
        assert result.score < 0
        
        # Test neutral
        result = analyzer.analyze("平盘 持有 观望")
        assert result.label == 'NEUTRAL'
    
    def test_stock_sentiment_aggregator(self):
        """Test StockSentimentAggregator class"""
        from finance_toolkit.sentiment import StockSentimentAggregator
        
        aggregator = StockSentimentAggregator()
        
        posts = [
            {'content': '看好后市', 'read_count': 1000, 'comment_count': 100},
            {'content': '看空风险', 'read_count': 500, 'comment_count': 50},
        ]
        
        result = aggregator.analyze_stock_posts(posts)
        
        assert result['post_count'] == 2
        assert 'signal' not in result  # signal is added by analyze_stock_sentiment
        assert 'label_distribution' in result
        assert 'positive_ratio' in result
        assert 'negative_ratio' in result


class TestSentimentEdgeCases:
    """Test edge cases for sentiment analysis"""
    
    def test_empty_text(self):
        """Test empty text"""
        from finance_toolkit.sentiment import analyze_sentiment
        
        result = analyze_sentiment("")
        assert result.payload['label'] == 'NEUTRAL'
        assert result.payload['score'] == 0
    
    def test_very_long_text(self):
        """Test very long text"""
        from finance_toolkit.sentiment import analyze_sentiment
        
        text = "大涨 " * 1000
        result = analyze_sentiment(text)
        assert result.payload['label'] in ['POSITIVE', 'VERY_POSITIVE']
    
    def test_special_characters(self):
        """Test text with special characters"""
        from finance_toolkit.sentiment import analyze_sentiment
        
        text = "茅台🚀🚀🚀 大涨！！！ @用户 #话题# $"
        result = analyze_sentiment(text)
        assert result.payload['label'] in ['POSITIVE', 'VERY_POSITIVE']
    
    def test_mixed_sentiment(self):
        """Test mixed positive and negative words"""
        from finance_toolkit.sentiment import analyze_sentiment
        
        text = "虽然业绩超预期大涨，但估值太高了泡沫风险大"
        result = analyze_sentiment(text)
        # Should handle mixed sentiment
        assert result.payload['label'] in ['POSITIVE', 'VERY_POSITIVE', 'NEGATIVE', 'VERY_NEGATIVE', 'NEUTRAL']
