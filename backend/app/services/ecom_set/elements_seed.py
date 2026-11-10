"""电商图元素库：系统预置元素（启动/首次请求时只补缺失，不覆盖后台改动）。

每行格式：key | 名称 | 匹配关键词（逗号分隔，用于按商品资料自动推荐）| 适合行业（逗号分隔，空=通用）| 尺度 s/m/l
给 AI 的描述默认用名称；需要更具体时写在 PROMPTS 里。
"""

from __future__ import annotations

CATEGORIES = {
    "plant": "植物花卉",
    "produce": "果蔬原料",
    "natural": "自然材质",
    "vessel": "器皿家居",
    "fabric": "布艺织物",
    "water": "水与质感",
    "light": "光影氛围",
    "festival": "节日道具",
}

_RAW = {
    "plant": """
pine_twig|松针|松,松针,冷杉,针叶,森林|daily,beauty|s
eucalyptus|尤加利枝|尤加利,桉|daily,beauty|m
olive_branch|橄榄枝|橄榄|daily,beauty,food|m
monstera|龟背竹叶片|龟背竹,绿植|daily,beauty,digital|l
fern|蕨类叶片|蕨,苔藓,森林|daily,beauty|m
camellia|白色山茶花|山茶|beauty,daily,jewelry|s
rose_petal|玫瑰花瓣|玫瑰,花香|beauty,daily,jewelry|s
rose|一枝玫瑰|玫瑰|beauty,daily,jewelry|m
peony|牡丹花|牡丹|beauty,daily|m
jasmine|茉莉花|茉莉|beauty,daily,tea|s
osmanthus|桂花枝|桂花|beauty,daily,tea,food|s
lavender|薰衣草束|薰衣草|beauty,daily|m
orange_blossom|橙花|橙花|beauty,daily|s
lily_valley|铃兰|铃兰|beauty,daily|s
freesia|小苍兰|小苍兰|beauty,daily|s
magnolia|玉兰花|玉兰|beauty,daily|m
cherry_blossom|樱花枝|樱花|beauty,daily,festival|m
dried_flower|干花束|干花|beauty,daily,jewelry|m
pampas|蒲苇|蒲苇|daily,beauty,digital|l
cotton_branch|棉花枝|棉花,棉|daily,beauty|m
gotu_kola|积雪草叶片|积雪草|beauty,daily|s
tea_leaf_fresh|新鲜茶叶嫩芽|茶,绿茶,茶叶|tea,beauty,daily|s
mint|薄荷叶|薄荷|food,daily,beauty|s
rosemary|迷迭香枝|迷迭香|food,daily,beauty|s
basil|罗勒叶|罗勒|food|s
bamboo_leaf|竹叶|竹|tea,daily|m
ginkgo|银杏叶|银杏|daily,beauty,tea|s
maple_leaf|枫叶|枫,秋|daily,festival|s
succulent|小多肉盆栽|多肉|daily,digital|s
moss|苔藓|苔藓,橡木苔,森林|daily,beauty|s
""",
    "produce": """
orange_slice|橙子切片|橙,柑橘,甜橙,巴伦西亚橙|daily,beauty,food|s
half_orange|半个鲜橙|橙,柑橘|daily,beauty,food|s
lemon_slice|柠檬切片|柠檬,香柠檬|daily,beauty,food|s
grapefruit|葡萄柚切片|葡萄柚,西柚|daily,beauty,food|s
bergamot|佛手柑|佛手柑,香柠檬|beauty,daily|s
lime|青柠|青柠|beauty,daily,food,liquor|s
peach|水蜜桃|桃,蜜桃,桃花|beauty,daily,food|s
fig|无花果切半|无花果|beauty,daily,food|s
pear|梨|梨|beauty,daily,food|s
strawberry|草莓|草莓|food,beauty|s
cherry|车厘子|樱桃,车厘子|food,beauty|s
grape|葡萄串|葡萄|food,liquor|s
apple|苹果|苹果|food,daily|s
cucumber|黄瓜切片|黄瓜|beauty,daily|s
avocado|牛油果切半|牛油果|beauty,food|s
coconut|椰子切半|椰子,椰|beauty,daily,food|s
shea|乳木果|乳木果|beauty,daily|s
honey|蜂蜜与蜂蜜勺|蜂蜜|beauty,food,tea|s
milk|牛奶飞溅|牛奶,奶|beauty,food|m
oat|燕麦粒|燕麦|beauty,food|s
rice|米粒|大米,米|beauty,food|s
coffee_bean|咖啡豆|咖啡|food,beauty|s
cocoa|可可豆|可可|food|s
vanilla|香草荚|香草|beauty,food|s
cinnamon|肉桂棒|肉桂|daily,beauty,food,festival|s
star_anise|八角|八角|food|s
cardamom|小豆蔻|豆蔻,小豆蔻|beauty,daily|s
ginger|生姜|姜,白姜|beauty,food|s
black_tea|红茶茶叶|红茶,伯爵|tea,beauty,daily|s
green_tea|绿茶茶叶|绿茶|tea,beauty|s
matcha|抹茶粉|抹茶|tea,food,beauty|s
rose_tea|干玫瑰花蕾|玫瑰花茶|tea,beauty|s
nuts|坚果|坚果,杏仁|food|s
almond|杏仁|杏仁,甜杏仁|beauty,food|s
wheat|麦穗|小麦,麦|food,liquor|m
""",
    "natural": """
travertine|洞石块|洞石|daily,beauty,jewelry|m
pebble|白色鹅卵石|鹅卵石|daily,beauty|s
rock|粗糙小岩石|岩石,石|daily,beauty|s
marble_block|大理石方块|大理石|beauty,jewelry,daily|m
old_wood|深棕色老木块|木,雪松,檀香,木质|daily,beauty|m
driftwood|漂流木|漂流木,海洋|daily,beauty|m
wood_slice|原木切片|原木,木|daily,beauty,food,tea|m
cork|软木|软木|daily,liquor|s
sand|细沙|沙,沙漠|daily,beauty,jewelry|m
shell|贝壳|贝壳,海洋|daily,beauty,jewelry|s
coral|白珊瑚|珊瑚,海洋|beauty,jewelry|s
crystal|白水晶簇|水晶|beauty,jewelry|s
amber|琥珀|琥珀|beauty,daily,jewelry|s
salt|海盐颗粒|海盐,盐|beauty,daily|s
pinecone|松果|松果,冬|daily,festival|s
feather|白色羽毛|羽毛,轻盈|beauty,jewelry,daily|s
""",
    "vessel": """
ceramic_tray|白色陶瓷托盘|陶瓷|daily,beauty,jewelry|m
brass_tray|黄铜托盘|黄铜|beauty,jewelry,daily|m
glass_dish|玻璃小碟|玻璃|beauty,jewelry|s
glass_vase|透明玻璃花瓶|花瓶|daily,beauty|m
pottery_vase|陶土花瓶|陶|daily,beauty,tea|m
wood_tray|原木托盘|木托|daily,food,tea|m
round_plinth|圆柱展示台|展台|beauty,jewelry,digital,daily|m
acrylic_block|透明亚克力方块|亚克力|beauty,jewelry,digital|m
books|几本书|书,阅读|daily,digital|m
candle|香薰蜡烛|蜡烛,香薰|daily,beauty,festival|s
mirror|圆形小镜子|镜子|beauty,jewelry|m
tea_cup|茶杯|茶杯,茶|tea,daily|s
wine_glass|红酒杯|红酒,酒杯|liquor|m
plate|白瓷餐盘|餐盘|food|m
spoon|木勺|木勺|food,beauty|s
jewelry_box|丝绒首饰盒|首饰盒|jewelry|s
keyboard|简约键盘一角|键盘,办公|digital|m
""",
    "fabric": """
linen|米白色亚麻布|亚麻|daily,beauty,food|m
silk|丝绸布料|丝绸,丝|beauty,jewelry,daily|m
velvet|丝绒布|丝绒|jewelry,beauty|m
towel|棉柔毛巾|毛巾,棉|daily,beauty|m
knit|针织毯|针织,冬|daily,festival|m
ribbon|缎带|缎带,礼物|beauty,jewelry,festival|s
paper|手工纸|纸|daily,tea,beauty|m
gauze|白色纱幔|纱|beauty,daily|l
""",
    "water": """
water_ripple|浅水面波纹|水,水生,海洋|daily,beauty|l
water_drops|水珠|水,保湿,水润|beauty,daily,food|s
splash|水花飞溅|水,水生|beauty,daily,food,liquor|m
ice|冰块|冰,冰雪,夏|beauty,food,liquor,daily|s
bubbles|泡泡|泡泡,沐浴|daily,beauty|s
foam|绵密泡沫|泡沫,洁面|beauty,daily|s
cream_swatch|膏体涂抹痕|膏,霜,乳|beauty,daily|s
oil_drop|透明油滴|精油,油|beauty|s
mist|水雾|雾,喷雾|beauty,daily|l
snow|雪花|雪,冬,冰雪|festival,beauty,daily|s
""",
    "light": """
leaf_shadow|墙面树叶光影|光影,绿植|daily,beauty|l
window_shadow|窗格投影|窗,光影|daily,beauty,jewelry|l
sunbeam|一束斜射阳光|阳光,晨|daily,beauty|l
dappled|斑驳光点|光斑|daily,beauty,food|l
candlelight|暖色烛光|烛光,夜|daily,beauty,festival,liquor|l
rim_light|轮廓逆光|逆光|beauty,jewelry,digital|l
neon|霓虹光晕|霓虹,夜|digital,liquor|l
rainbow|彩虹折射光|折射,水晶|beauty,jewelry|l
""",
    "festival": """
xmas_tree|迷你圣诞树|圣诞|festival,daily,beauty|m
xmas_ball|圣诞彩球|圣诞|festival|s
gift_box|礼物盒|礼物,礼盒,送礼|festival,beauty,jewelry,daily|m
red_envelope|红包|春节,新年|festival|s
lantern|小灯笼|春节,中秋|festival,food,tea|m
mooncake|月饼|中秋|festival,food|s
heart|爱心装饰|情人节,爱|festival,jewelry,beauty|s
confetti|彩色纸屑|派对,庆祝|festival|s
summer_hat|草编帽|夏,度假|festival,daily|m
spring_blossom|春日花枝|春|festival,beauty,daily|m
""",
}

PROMPTS = {
    "water_ripple": "商品旁或底部有一片平静的浅水面，带柔和波纹与倒影",
    "cream_swatch": "台面上一道自然抹开的膏体涂抹痕，质地细腻",
    "leaf_shadow": "墙面与台面上有窗外植物投下的斜向叶影",
    "window_shadow": "墙面与台面上有窗格投下的几何光影",
    "sunbeam": "一束斜射的阳光照亮商品局部",
    "milk": "旁边有一小股牛奶溅起的奶花",
    "splash": "旁边有清澈的水花溅起，动态感",
    "rim_light": "商品边缘有一圈柔和的逆光轮廓",
}

INDUSTRY_ALL = ["daily", "beauty", "food", "tea", "liquor", "digital", "jewelry"]


def _expand(raw: dict[str, str], prompts: dict[str, str], offset: int) -> list[dict]:
    out: list[dict] = []
    for cat, block in raw.items():
        for i, line in enumerate(x for x in block.strip().splitlines() if x.strip()):
            key, name, kws, inds, scale = (p.strip() for p in line.split("|"))
            industries = [x for x in inds.split(",") if x and x != "festival"] or INDUSTRY_ALL
            out.append({
                "key": f"el_{key}", "name": name, "category": cat, "prompt": prompts.get(key, name),
                "keywords": [k for k in kws.split(",") if k], "industries": industries, "scale": scale,
                "sort_order": offset + i * 10,
            })
    return out


def system_elements() -> list[dict]:
    """展开为结构化列表：key / name / category / prompt / keywords / industries / scale / sort_order。

    通用元素在前，节日元素（festivals_seed）排在各分类末尾。
    """
    from app.services.ecom_set.festivals_seed import FESTIVAL_PROMPTS, FESTIVAL_RAW

    return _expand(_RAW, PROMPTS, 0) + _expand(FESTIVAL_RAW, FESTIVAL_PROMPTS, 1000)
