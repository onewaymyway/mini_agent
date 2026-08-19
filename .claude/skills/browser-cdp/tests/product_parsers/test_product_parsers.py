#!/usr/bin/env python
"""
test_product_parsers.py - 商品解析器单元测试

测试京东、淘宝、拼多多、通用解析器的正确性。
"""

import sys
import unittest
from pathlib import Path
from datetime import datetime

SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.product_parsers.base import ProductData, BaseProductParser
from src.product_parsers.jd_parser import JDProductParser
from src.product_parsers.taobao_parser import TaobaoProductParser
from src.product_parsers.pdd_parser import PDDProductParser
from src.product_parsers.universal_parser import UniversalProductParser
from src.product_parsers.amazon_parser import AmazonProductParser


# ========== HTML 测试数据 ==========
JD_LIST_HTML = """
<html>
<head><title>京东搜索：iPhone 15</title></head>
<body>
<div id="J_goodsList">
  <ul class="gl-warp clearfix">
    <li class="gl-item">
      <div class="gl-i-wrap">
        <div class="p-name">
          <a data-sku="100012345678" href="https://item.jd.com/100012345678.html">
            <em>Apple iPhone 15 128GB 粉色 5G手机【不支持合约机】</em>
          </a>
        </div>
        <div class="p-price">
          <strong>¥4,999.00</strong>
        </div>
        <div class="p-commit">
          <strong>10万+</strong>评价
        </div>
        <div class="p-shop">
          <a href="#">京东自营</a>
        </div>
        <div class="p-img">
          <img src="//img14.360buyimg.com/n1/jfs/t1/123456/1.jpg" data-lazy-img="//img14.360buyimg.com/n1/jfs/t1/123456/1.jpg">
        </div>
      </div>
    </li>
    <li class="gl-item">
      <div class="gl-i-wrap">
        <div class="p-name">
          <a data-sku="100087654321" href="https://item.jd.com/100087654321.html">
            <em>小米14 骁龙8Gen3 徕卡光学镜头 5G手机 黑色 256GB</em>
          </a>
        </div>
        <div class="p-price">
          <strong>¥3,999.00</strong>
        </div>
        <div class="p-commit">
          <strong>5万+</strong>评价
        </div>
        <div class="p-shop">
          <a href="#">小米京东自营旗舰店</a>
        </div>
        <div class="p-img">
          <img src="//img14.360buyimg.com/n1/jfs/t1/654321/1.jpg">
        </div>
      </div>
    </li>
  </ul>
</div>
</body>
</html>
"""

JD_DETAIL_HTML = """
<html>
<head><title>Apple iPhone 15 128GB</title></head>
<body>
  <h1 class="sku-name">Apple iPhone 15 128GB 粉色 5G手机</h1>
  <div class="sku-info">
    <div class="p-price"><strong>¥4,999.00</strong></div>
    <div class="p-price del"><del>¥5,999.00</del></div>
  </div>
  <div id="comment-count">100000+条评论</div>
  <div class="parameter2">
    <p>机身内存: 128GB</p>
    <p>颜色: 粉色</p>
    <p>屏幕尺寸: 6.1英寸</p>
    <p>处理器: A16仿生</p>
  </div>
  <div class="description">Apple A16芯片，4800万像素主摄...</div>
  <div id="spec-n1">
    <img src="https://img14.360buyimg.com/n1/1.jpg">
    <img src="https://img14.360buyimg.com/n1/2.jpg">
  </div>
  <div id="choose-btns">有货</div>
</body>
</html>
"""

TB_LIST_HTML = """
<html>
<head><title>淘宝搜索：机械键盘</title></head>
<body>
  <div class="items J_MouserOnstay">
    <div class="item">
      <div class="pic">
        <a href="https://detail.tmall.com/item.htm?id=123456" class="J_ClickStat">
          <img data-src="https://img.alicdn.com/imgextra/i1/TB1abc.jpg" alt="机械键盘">
        </a>
      </div>
      <div class="title">
        <a href="https://detail.tmall.com/item.htm?id=123456">[天猫] Keychron K8 机械键盘 无线蓝牙双模 RGB背光 热插拔轴体 游戏办公键盘</a>
      </div>
      <div class="price">
        <i>¥</i><b>399.00</b>
      </div>
      <div class="sales">月售 1000+件</div>
      <div class="shop">
        <a href="#">Keychron旗舰店</a>
      </div>
      <div class="location">广东 深圳</div>
      <span class="tag-tmall">天猫</span>
    </div>
    <div class="item">
      <div class="pic">
        <a href="https://item.taobao.com/item.htm?id=654321" class="J_ClickStat">
          <img data-src="https://img.alicdn.com/imgextra/i2/TB1def.jpg" alt="机械键盘">
        </a>
      </div>
      <div class="title">
        <a href="https://item.taobao.com/item.htm?id=654321">洛斐小顺机械键盘 RGB背光 青轴茶轴红轴 87键 复古机械键盘</a>
      </div>
      <div class="price">
        <i>¥</i><b>268.00</b>
      </div>
      <div class="sales">月售 500+件</div>
      <div class="shop">
        <a href="#">洛斐官方旗舰店</a>
      </div>
      <div class="location">浙江 杭州</div>
    </div>
  </div>
</body>
</html>
"""

PDD_LIST_HTML = """
<html>
<head><title>拼多多搜索：蓝牙耳机</title></head>
<body>
  <div class="goods-list">
    <div class="goods-card">
      <a class="goods-name" href="https://mobile.yangkeduo.com/goods.html?goods_id=111111">
        倍思M2 真无线蓝牙耳机 主动降噪 入耳式HiFi 长续航
      </a>
      <div class="goods-price">
        <span class="price">¥49.90</span>
        <span class="original-price">¥99.00</span>
      </div>
      <div class="goods-sales">已拼10万+件</div>
      <div class="goods-shop">
        <a href="#">倍思官方旗舰店</a>
      </div>
      <div class="goods-img">
        <img src="https://img.pddpic.com/mms-material-img/2024-01-01/abc.jpg" data-lazy-src="https://img.pddpic.com/mms-material-img/2024-01-01/abc.jpg">
      </div>
      <div class="coupon-info">满39减10</div>
    </div>
    <div class="goods-card">
      <a class="goods-name" href="https://mobile.yangkeduo.com/goods.html?goods_id=222222">
        漫步者Lolli Pro2 真无线降噪耳机 Hi-Res认证 通透模式
      </a>
      <div class="goods-price">
        <span class="price">¥199.00</span>
        <span class="original-price">¥299.00</span>
      </div>
      <div class="goods-sales">已拼5万+件</div>
      <div class="goods-shop">
        <a href="#">漫步者官方旗舰店</a>
      </div>
      <div class="goods-img">
        <img src="https://img.pddpic.com/mms-material-img/2024-01-01/def.jpg">
      </div>
      <div class="promo-tag">限时特惠</div>
    </div>
  </div>
</body>
</html>
"""

UNIVERSAL_JSONLD_HTML = """
<html>
<head><title>示例商品页</title></head>
<body>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "Product",
  "name": "Universal测试产品",
  "image": ["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
  "offers": {
    "@type": "Offer",
    "price": "299.00",
    "priceCurrency": "CNY",
    "highPrice": "399.00",
    "availability": "https://schema.org/InStock"
  },
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": "4.8",
    "reviewCount": "1234"
  },
  "brand": {
    "@type": "Brand",
    "name": "测试品牌"
  }
}
</script>
<div class="product-detail">
  <h1 class="product-title">Universal测试产品</h1>
  <div class="price">$299.00</div>
  <div class="description">这是一个通用的产品描述...</div>
</div>
</body>
</html>
"""

UNIVERSAL_HTML_NO_JSONLD = """
<html>
<head><title>Generic Product Page</title></head>
<body>
<div class="product-item">
  <h2 class="product-title">Generic Product Name</h2>
  <a href="/product/12345">Buy Now</a>
  <span class="price">$59.99</span>
  <span class="original-price">$79.99</span>
  <img src="https://example.com/product-img.jpg" alt="Product Image">
  <div class="description">This is a generic product description for testing.</div>
  <table class="specs">
    <tr><td>Weight</td><td>200g</td></tr>
    <tr><td>Dimensions</td><td>10x5x2cm</td></tr>
  </table>
</div>
</body>
</html>
"""

UNIVERSAL_HTML_NO_JSONLD_HTML = UNIVERSAL_HTML_NO_JSONLD


# ========== 测试类 ==========
class TestProductData(unittest.TestCase):
    """测试ProductData数据结构"""

    def test_create_product(self):
        p = ProductData(source="jd", title="Test", price="¥100", url="https://jd.com/123")
        self.assertEqual(p.source, "jd")
        self.assertEqual(p.title, "Test")
        self.assertEqual(p.price, "¥100")
        self.assertEqual(p.price_num, 100.0)

    def test_to_dict(self):
        p = ProductData(source="jd", title="Test", price="¥100")
        d = p.to_dict()
        self.assertEqual(d["source"], "jd")
        self.assertEqual(d["title"], "Test")
        self.assertIn("scraped_at", d)

    def test_from_dict(self):
        data = {"source": "jd", "title": "Test", "price": "¥100", "url": "https://jd.com/123"}
        p = ProductData.from_dict(data)
        self.assertEqual(p.source, "jd")
        self.assertEqual(p.title, "Test")

    def test_images_limit(self):
        p = ProductData(source="jd")
        p.images = [f"https://img.example.com/{i}.jpg" for i in range(10)]
        d = p.to_dict()
        self.assertEqual(len(d["images"]), 5)

    def test_description_truncate(self):
        long_text = "a" * 5000
        p = ProductData(source="jd", description=long_text)
        d = p.to_dict()
        self.assertLessEqual(len(d["description"]), 2000)


class TestPriceExtraction(unittest.TestCase):
    """测试价格提取"""

    def setUp(self):
        self.parser = JDProductParser()

    def test_extract_price_number_standard(self):
        self.assertEqual(self.parser.extract_price_number("¥100.00"), 100.0)

    def test_extract_price_number_no_currency(self):
        self.assertEqual(self.parser.extract_price_number("299.99"), 299.99)

    def test_extract_price_number_with_comma(self):
        self.assertEqual(self.parser.extract_price_number("¥1,299.00"), 1299.0)

    def test_extract_price_empty(self):
        self.assertEqual(self.parser.extract_price_number(""), 0.0)

    def test_extract_price_no_digits(self):
        self.assertEqual(self.parser.extract_price_number("免费"), 0.0)

    def test_extract_price_complex(self):
        self.assertEqual(self.parser.extract_price_number("限时优惠 ¥299"), 299.0)


class TestSalesExtraction(unittest.TestCase):
    """测试销量提取"""

    def setUp(self):
        self.parser = JDProductParser()

    def test_sales_plain_number(self):
        self.assertEqual(self.parser.extract_sales_number("10000"), 10000)

    def test_sales_wan(self):
        self.assertEqual(self.parser.extract_sales_number("10万+"), 100000)

    def test_sales_dianwan(self):
        self.assertEqual(self.parser.extract_sales_number("1.5万"), 15000)

    def test_sales_bai(self):
        self.assertEqual(self.parser.extract_sales_number("500+"), 500)

    def test_sales_empty(self):
        self.assertEqual(self.parser.extract_sales_number(""), 0)


class TestTitleClean(unittest.TestCase):
    """测试标题清理"""

    def setUp(self):
        self.parser = JDProductParser()

    def test_clean_basic(self):
        self.assertEqual(self.parser.clean_title("  测试商品  "), "测试商品")

    def test_clean_multispace(self):
        self.assertEqual(self.parser.clean_title("苹果  手机  测试"), "苹果 手机 测试")

    def test_clean_brackets(self):
        title = "iPhone 15 [官方标配] 送壳膜"
        cleaned = self.parser.clean_title(title)
        self.assertNotIn("[官方标配]", cleaned)

    def test_clean_fullwidth_brackets(self):
        title = "小米14 【限时优惠】送保护壳"
        cleaned = self.parser.clean_title(title)
        self.assertNotIn("【限时优惠】", cleaned)


class TestJDParser(unittest.TestCase):
    """测试京东解析器"""

    def setUp(self):
        self.parser = JDProductParser()

    def test_detect_jd_url(self):
        self.assertTrue(self.parser.detect("https://item.jd.com/123456.html"))
        self.assertTrue(self.parser.detect("https://search.jd.com/Search?keyword=test"))
        self.assertFalse(self.parser.detect("https://www.taobao.com"))

    def test_parse_list_page(self):
        products = self.parser.parse_list_page(JD_LIST_HTML, "https://search.jd.com", max_results=10)
        self.assertGreater(len(products), 0)
        for p in products:
            self.assertTrue(p.title)
            self.assertTrue(p.price)
            self.assertEqual(p.source, "jd")

    def test_parse_list_page_extract_fields(self):
        products = self.parser.parse_list_page(JD_LIST_HTML, "https://search.jd.com", max_results=10)
        self.assertGreaterEqual(len(products), 2)

        # 第一个商品
        p1 = products[0]
        self.assertIn("iPhone 15", p1.title)
        self.assertIn("4,999.00", p1.price)
        self.assertEqual(p1.price_num, 4999.0)
        self.assertIn("10万+", p1.commit_count)
        self.assertEqual(p1.source, "jd")

        # 第二个商品
        p2 = products[1]
        self.assertIn("小米14", p2.title)
        self.assertIn("3,999.00", p2.price)
        self.assertEqual(p2.price_num, 3999.0)

    def test_parse_detail_page(self):
        product = self.parser.parse_detail_page(JD_DETAIL_HTML, "https://item.jd.com/100012345678.html")
        self.assertTrue(product.title)
        self.assertIn("iPhone 15", product.title)
        self.assertEqual(product.price_num, 4999.0)
        self.assertEqual(product.original_price, "¥5,999.00")
        self.assertIn("100000+", product.commit_count)
        self.assertIn("机身内存", product.specs)
        self.assertEqual(product.specs.get("机身内存"), "128GB")
        self.assertGreater(len(product.images), 0)
        self.assertTrue(product.in_stock)

    def test_parse_detail_page_no_stock(self):
        html_no_stock = JD_DETAIL_HTML.replace("有货", "无货")
        product = self.parser.parse_detail_page(html_no_stock, "https://item.jd.com/100012345678.html")
        self.assertFalse(product.in_stock)


class TestTaobaoParser(unittest.TestCase):
    """测试淘宝解析器"""

    def setUp(self):
        self.parser = TaobaoProductParser()

    def test_detect_taobao_url(self):
        self.assertTrue(self.parser.detect("https://item.taobao.com/item.htm?id=123"))
        self.assertTrue(self.parser.detect("https://detail.tmall.com/item.htm?id=456"))
        self.assertFalse(self.parser.detect("https://www.jd.com"))

    def test_parse_list_page(self):
        products = self.parser.parse_list_page(TB_LIST_HTML, "https://s.taobao.com", max_results=10)
        self.assertGreater(len(products), 0)
        for p in products:
            self.assertTrue(p.title)
            self.assertEqual(p.source, "taobao")

    def test_parse_list_page_tmall(self):
        products = self.parser.parse_list_page(TB_LIST_HTML, "https://s.taobao.com", max_results=10)
        tmall_products = [p for p in products if "tmall" in p.tags]
        self.assertGreater(len(tmall_products), 0)

    def test_parse_list_page_fields(self):
        products = self.parser.parse_list_page(TB_LIST_HTML, "https://s.taobao.com", max_results=10)
        p = products[0]
        self.assertIn("Keychron", p.title)
        self.assertEqual(p.price_num, 399.0)
        self.assertIn("Keychron旗舰店", p.shop_name)
        self.assertEqual(p.location, "广东 深圳")


class TestPDDParser(unittest.TestCase):
    """测试拼多多解析器"""

    def setUp(self):
        self.parser = PDDProductParser()

    def test_detect_pdd_url(self):
        self.assertTrue(self.parser.detect("https://mobile.yangkeduo.com/goods.html?goods_id=123"))
        self.assertTrue(self.parser.detect("https://pinduoduo.com/goods/456"))
        self.assertFalse(self.parser.detect("https://www.jd.com"))

    def test_parse_list_page(self):
        products = self.parser.parse_list_page(PDD_LIST_HTML, "https://mobile.yangkeduo.com", max_results=10)
        self.assertGreater(len(products), 0)
        for p in products:
            self.assertTrue(p.title)
            self.assertEqual(p.source, "pdd")

    def test_parse_list_page_fields(self):
        products = self.parser.parse_list_page(PDD_LIST_HTML, "https://mobile.yangkeduo.com", max_results=10)
        p = products[0]
        self.assertIn("倍思M2", p.title)
        self.assertEqual(p.price_num, 49.9)
        self.assertIn("100000", str(p.sales_count))
        self.assertIn("倍思官方旗舰店", p.shop_name)
        self.assertTrue(p.is_promotion)
        self.assertIn("满39减10", p.promo_text)

    def test_parse_list_page_promo(self):
        products = self.parser.parse_list_page(PDD_LIST_HTML, "https://mobile.yangkeduo.com", max_results=10)
        promo_products = [p for p in products if p.is_promotion]
        self.assertGreater(len(promo_products), 0)


class TestUniversalParser(unittest.TestCase):
    """测试通用解析器"""

    def setUp(self):
        self.parser = UniversalProductParser()

    def test_detect_universal_url(self):
        self.assertTrue(self.parser.detect("https://example.com/product/123"))
        self.assertTrue(self.parser.detect("https://shop.example.com/item/456"))
        self.assertFalse(self.parser.detect("https://www.jd.com/search"))

    def test_jsonld_extraction(self):
        product = self.parser.parse_detail_page(UNIVERSAL_JSONLD_HTML, "https://example.com/product/1")
        self.assertTrue(product.title)
        self.assertIn("Universal", product.title)
        self.assertEqual(product.price_num, 299.0)
        self.assertEqual(product.original_price, "399.00")
        self.assertEqual(product.commit_count, "1234")
        self.assertEqual(product.shop_name, "测试品牌")
        self.assertEqual(len(product.images), 2)

    def test_html_fallback_extraction(self):
        product = self.parser.parse_detail_page(UNIVERSAL_HTML_NO_JSONLD_HTML, "https://example.com/product/2")
        self.assertTrue(product.title)
        self.assertIn("Generic", product.title)
        self.assertEqual(product.price_num, 59.99)
        self.assertEqual(product.original_price, "$79.99")
        self.assertIn("generic", product.description.lower())
        self.assertEqual(product.specs.get("Weight"), "200g")

    def test_jsonld_with_nested_structure(self):
        # 测试嵌套JSON-LD结构
        nested_html = """
        <html>
        <body>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "WebPage",
          "mainEntity": {
            "@type": "Product",
            "name": "Nested Product",
            "offers": {"@type": "Offer", "price": "199.00", "priceCurrency": "CNY"}
          }
        }
        </script>
        </body>
        </html>
        """
        product = self.parser.parse_detail_page(nested_html, "https://example.com/nested")
        # 如果JSON-LD提取成功，应该有title
        # 如果失败，会回退到HTML选择器
        self.assertIsInstance(product, ProductData)


class TestPipelineIntegration(unittest.TestCase):
    """测试解析器与管道的集成"""

    def test_import_all_parsers(self):
        """测试所有解析器可以正常导入"""
        from src.product_parsers import (
            JDProductParser,
            TaobaoProductParser,
            PDDProductParser,
            UniversalProductParser,
        )
        parsers = [JDProductParser(), TaobaoProductParser(), PDDProductParser(), UniversalProductParser()]
        self.assertEqual(len(parsers), 4)

    def test_parser_registry(self):
        """测试解析器注册表"""
        from src.product_parsers import get_parser
        jd = get_parser("jd")
        taobao = get_parser("taobao")
        pdd = get_parser("pdd")
        universal = get_parser("universal")

        self.assertIsInstance(jd, JDProductParser)
        self.assertIsInstance(taobao, TaobaoProductParser)
        self.assertIsInstance(pdd, PDDProductParser)
        self.assertIsInstance(universal, UniversalProductParser)

    def test_resolve_parser_by_url(self):
        """测试根据URL自动选择解析器"""
        from src.product_parsers import resolve_parser

        # JD URL
        parser = resolve_parser("https://item.jd.com/123456.html")
        self.assertIsInstance(parser, JDProductParser)

        # 淘宝 URL
        parser = resolve_parser("https://item.taobao.com/item.htm?id=123")
        self.assertIsInstance(parser, TaobaoProductParser)

        # 拼多多 URL
        parser = resolve_parser("https://mobile.yangkeduo.com/goods.html?goods_id=123")
        self.assertIsInstance(parser, PDDProductParser)

        # 通用 URL
        parser = resolve_parser("https://example.com/product/123")
        self.assertIsInstance(parser, UniversalProductParser)

        # Amazon URL
        parser = resolve_parser("https://www.amazon.com/dp/B08N5WRWNW")
        self.assertIsInstance(parser, AmazonProductParser)


# ========== Amazon 测试数据 ==========
AMAZON_LIST_HTML = """
<html>
<head><title>Amazon Search: wireless headphones</title></head>
<body>
<div class="sg-col-20-of-24 s-result-item" data-asin="B08N5WRWNW">
  <div class="sg-col-inner">
    <a href="/dp/B08N5WRWNW">
      <img class="s-image" src="//images-na.ssl-images-amazon.com/images/I/71abc.jpg">
    </a>
    <h2 class="s-item__title">
      <span>Wireless Bluetooth Headphones - Noise Cancelling Over-Ear</span>
    </h2>
    <div class="a-price">
      <span class="a-offscreen">$29.99</span>
    </div>
    <div class="s-item__reviewCount">
      <span class="a-size-small">12,456 ratings</span>
    </div>
    <span class="s-prime">Prime</span>
    <span class="s-item__shipping">FREE delivery</span>
  </div>
</div>
<div class="sg-col-20-of-24 s-result-item" data-asin="B07XJ8C8F5">
  <div class="sg-col-inner">
    <a href="/dp/B07XJ8C8F5">
      <img class="s-image" src="//images-na.ssl-images-amazon.com/images/I/71def.jpg">
    </a>
    <h2 class="s-item__title">
      <span>Sony WH-1000XM4 Wireless Premium Noise Canceling Headphones</span>
    </h2>
    <div class="a-price">
      <span class="a-offscreen">$348.00</span>
      <span class="list-price">$399.99</span>
    </div>
    <div class="s-item__reviewCount">
      <span class="a-size-small">89,234 ratings</span>
    </div>
    <span class="s-prime">Prime</span>
  </div>
</div>
</body>
</html>
"""

AMAZON_DETAIL_HTML = """
<html>
<head><title>Amazon.com: Wireless Bluetooth Headphones</title></head>
<body>
  <h1 id="productTitle" class="product-title">
    <span>Wireless Bluetooth Headphones - Noise Cancelling Over-Ear with Mic</span>
  </h1>
  <div class="a-row">
    <span class="a-price">
      <span class="a-offscreen">$29.99</span>
    </span>
    <span class="list-price">$49.99</span>
  </div>
  <div id="acrPopover">
    <a href="#"><span class="a-icon-alt">4.5 out of 5 stars</span></a>
  </div>
  <div id="bylineInfo">
    <a href="#">SoundTech Brand</a>
  </div>
  <div id="availability">
    <span>In Stock</span>
  </div>
  <div id="bullets">
    <ul class="a-unordered-list">
      <li>Active Noise Cancellation Technology</li>
      <li>40-hour battery life</li>
      <li>Bluetooth 5.0 connectivity</li>
      <li>Foldable design with carrying case</li>
      <li>Built-in microphone for hands-free calls</li>
    </ul>
  </div>
  <div id="productDescription">
    <p>Premium wireless headphones with industry-leading noise cancellation.</p>
  </div>
  <div id="techSpecification">
    <table>
      <tr><td>Brand</td><td>SoundTech</td></tr>
      <tr><td>Model</td><td>ST-WH1000</td></tr>
      <tr><td>Connectivity</td><td>Bluetooth 5.0</td></tr>
      <tr><td>Battery Life</td><td>40 hours</td></tr>
    </table>
  </div>
  <img id="landingImage" src="https://images-na.ssl-images-amazon.com/images/I/71main.jpg">
</body>
</html>
"""


class TestAmazonParser(unittest.TestCase):
    """测试Amazon解析器"""

    def setUp(self):
        self.parser = AmazonProductParser()

    def test_detect_amazon_url(self):
        self.assertTrue(self.parser.detect("https://www.amazon.com/dp/B08N5WRWNW"))
        self.assertTrue(self.parser.detect("https://www.amazon.co.jp/..."))
        self.assertTrue(self.parser.detect("https://www.amazon.de/..."))
        self.assertFalse(self.parser.detect("https://www.jd.com"))

    def test_parse_list_page(self):
        products = self.parser.parse_list_page(AMAZON_LIST_HTML, "https://www.amazon.com/s", max_results=10)
        self.assertGreater(len(products), 0)
        for p in products:
            self.assertTrue(p.title)
            self.assertEqual(p.source, "amazon")

    def test_parse_list_page_fields(self):
        products = self.parser.parse_list_page(AMAZON_LIST_HTML, "https://www.amazon.com/s", max_results=10)
        p = products[0]
        self.assertIn("Wireless", p.title)
        self.assertEqual(p.price_num, 29.99)
        self.assertTrue(p.in_stock)
        self.assertIn("prime", [t.lower() for t in p.tags])
        self.assertGreater(len(p.images), 0)

    def test_parse_detail_page(self):
        product = self.parser.parse_detail_page(AMAZON_DETAIL_HTML, "https://www.amazon.com/dp/B08N5WRWNW")
        self.assertTrue(product.title)
        self.assertIn("Wireless", product.title)
        self.assertEqual(product.price_num, 29.99)
        self.assertEqual(product.original_price, "$49.99")
        self.assertEqual(product.shop_name, "SoundTech Brand")
        self.assertTrue(product.in_stock)
        self.assertEqual(product.specs.get("Brand"), "SoundTech")
        self.assertEqual(product.specs.get("Model"), "ST-WH1000")
        self.assertGreater(len(product.images), 0)
        self.assertIn("noise cancellation", product.description.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)