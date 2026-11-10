"""节日场景（26 个）与节日元素（约 85 个）的系统预置数据。启动时只补缺失，不覆盖后台改动。

节日场景面向全部行业；元素格式同 elements_seed：key | 名称 | 匹配关键词 | 适合行业（空=通用）| 尺度。
关键词只写物件本身，不写季节词，避免按商品资料推荐时误命中。
"""

from __future__ import annotations

from app.services.ecom_set.scenes_seed import _s

_ALL = ["daily", "beauty", "food", "tea", "liquor", "digital", "jewelry"]


def _fs(key: str, name: str, desc: str, **spec) -> dict:
    return _s(key, name, _ALL, desc, **spec)


FESTIVAL_SCENES: list[dict] = [
    _fs("fest_yuandan", "元旦跨年", "香槟金 + 炭黑，彩带亮片迎新年",
        backdrop="深炭黑色背景，远处有虚化的金色光斑", palette="炭黑、香槟金", surface="黑色哑光台面",
        props=["金色彩带", "金色亮片", "香槟杯"], light="暖金色侧光，高光闪烁", mood="庆祝、闪耀、迎新", bg="2A2622"),
    _fs("fest_nianhuo", "年货节", "正红暖光 + 红针织毯 + 礼盒红果",
        backdrop="暖红色渐变背景，上浅下深，柔和暖光", palette="中国红、暖杏", surface="铺着红色粗针织毯的台面",
        props=["红色礼盒", "红果枝", "砂糖橘"], light="温暖的侧逆光", mood="喜庆、温暖、年味十足", bg="B8352B"),
    _fs("fest_chunjie", "新春红金", "中国红 + 金，中国结红包，喜庆大气",
        backdrop="中国红素色背景，带细腻纸质纹理", palette="中国红、金", surface="红色弧形纸艺展台",
        props=["中国结", "红包", "红梅枝"], light="柔和顶光加暖色侧光", mood="喜庆、大气、团圆", bg="A82820"),
    _fs("fest_chunjie_cozy", "新春暖冬", "红色针织 + 砂糖橘车厘子，居家年味",
        backdrop="米白色台面上堆着大片红色粗针织毛毯", palette="砖红、米白、橘色", surface="米白色桌面，一侧堆叠红色针织毯",
        props=["砂糖橘", "车厘子", "红果"], light="柔和自然窗光", mood="温暖、居家、松弛的新年氛围", bg="B5463A"),
    _fs("fest_yuanxiao", "元宵灯会", "暖黄花灯 + 汤圆，团圆夜",
        backdrop="深红夜色背景，远处有虚化的暖黄色花灯光点", palette="深红、暖黄", surface="深色木台",
        props=["花灯", "一碗汤圆"], light="暖黄灯光从侧面照来", mood="团圆、温馨、灯火", bg="5E1D1A"),
    _fs("fest_valentine", "情人节", "酒红丝绒 + 玫瑰巧克力，浪漫心动",
        backdrop="酒红色丝绒质感背景", palette="酒红、玫瑰粉", surface="酒红色丝绒台面",
        props=["一枝红玫瑰", "爱心巧克力", "缎带"], light="柔和侧光带一点暖色烛光", mood="浪漫、心动、精致", bg="6E1F2A"),
    _fs("fest_women", "女神节", "珍珠粉 + 郁金香，宠爱自己",
        backdrop="浅珍珠粉色背景", palette="珍珠粉、香槟色", surface="香槟色丝绸",
        props=["珍珠串", "郁金香"], light="柔和散射光", mood="优雅、自信、宠爱自己", bg="EBD3CF"),
    _fs("fest_spring", "春日踏青", "樱花嫩芽 + 野餐布，春日明媚",
        backdrop="明亮的浅绿到天空蓝渐变，远处有虚化的樱花树", palette="嫩绿、樱花粉", surface="草地上的格子野餐布",
        props=["樱花枝", "嫩芽", "藤编篮"], light="明媚的春日阳光", mood="明亮、轻快、生机", bg="DCE8D2"),
    _fs("fest_labor", "五一出游", "阳光户外 + 草帽太阳镜，轻装出发",
        backdrop="明亮的户外阳光背景，虚化的绿植", palette="暖黄、天蓝", surface="浅色原木桌面",
        props=["草编帽", "太阳镜", "旅行地图"], light="明亮的午后阳光", mood="自由、轻松、度假", bg="EFE2C4"),
    _fs("fest_mother", "母亲节", "柔粉 + 康乃馨手写卡片，温柔感恩",
        backdrop="柔粉色素色背景", palette="柔粉、奶白", surface="白色大理石台面",
        props=["康乃馨", "手写卡片", "礼盒"], light="柔和窗光", mood="温柔、感恩、温暖", bg="EED7D3"),
    _fs("fest_520", "520 告白", "粉红爱心 + 玫瑰花瓣，甜蜜告白",
        backdrop="粉红色渐变背景", palette="粉红、正红", surface="粉色亚克力台",
        props=["爱心气球", "玫瑰花瓣", "礼盒"], light="明亮柔光", mood="甜蜜、心动、热烈", bg="F0C5C8"),
    _fs("fest_children", "儿童节", "糖果色 + 积木气球，童趣满满",
        backdrop="糖果色渐变背景（浅蓝到浅黄）", palette="糖果色", surface="彩色几何积木台",
        props=["彩色积木", "气球", "糖果"], light="明亮柔光", mood="童趣、快乐、明亮", bg="CDE4F0"),
    _fs("fest_618", "618 年中大促", "明亮暖色 + 礼盒彩带，大促热闹",
        backdrop="明亮的橙红到暖黄渐变背景", palette="橙红、明黄", surface="白色圆柱展台",
        props=["叠放的礼盒", "彩色气球", "彩带"], light="明亮硬光，投影清晰", mood="热闹、超值、活力", bg="F2A14A"),
    _fs("fest_duanwu", "端午安康", "青竹 + 粽子艾草，清雅东方",
        backdrop="浅青绿色背景，有竹叶投下的光影", palette="青绿、竹黄", surface="竹编托盘",
        props=["粽子", "艾草菖蒲", "五彩绳"], light="柔和自然光，竹影斑驳", mood="清雅、安康、东方", bg="CFDCC9"),
    _fs("fest_father", "父亲节", "深蓝木色 + 皮革腕表，沉稳质感",
        backdrop="深海军蓝背景", palette="海军蓝、胡桃木色", surface="深色胡桃木桌面",
        props=["皮革小物", "腕表一角", "几本书"], light="侧光，明暗分明", mood="沉稳、可靠、质感", bg="25324A"),
    _fs("fest_summer", "夏日清凉", "冰块西瓜柠檬水，清凉一夏",
        backdrop="清透的冰蓝色背景，有水波光影", palette="冰蓝、柠檬黄", surface="浅水面",
        props=["冰块", "柠檬切片", "西瓜切块"], light="明亮的夏日阳光，水波光斑", mood="清凉、清爽、活力", bg="C6E2EA"),
    _fs("fest_qixi", "七夕", "深蓝星空 + 红线喜鹊，东方浪漫",
        backdrop="深蓝色夜空背景，有虚化的星星灯光", palette="深蓝、红、暖金", surface="深色丝绒台面",
        props=["星星灯串", "红线同心结", "一枝红玫瑰"], light="暖色点光，星光闪烁", mood="浪漫、东方、静谧", bg="1E2747"),
    _fs("fest_school", "开学季", "清爽书桌 + 笔记本铅笔，新学期",
        backdrop="浅米色墙面，有窗格光影", palette="米白、浅蓝、木色", surface="浅色木书桌",
        props=["笔记本", "铅笔", "几本书"], light="清晨窗光", mood="清爽、元气、新开始", bg="E8E2D6"),
    _fs("fest_zhongqiu", "中秋团圆", "墨蓝夜色 + 圆月桂花，月圆人团圆",
        backdrop="墨蓝色夜色背景，有一轮柔和的暖黄色圆月", palette="墨蓝、暖金", surface="深色石台",
        props=["月饼", "桂花枝", "玉兔摆件"], light="月光般的柔和暖光", mood="团圆、诗意、温润", bg="1F2E40"),
    _fs("fest_guoqing", "国庆金秋", "红金绸缎 + 祥云灯笼，大气欢庆",
        backdrop="红色到金色的渐变背景", palette="正红、金", surface="红金色绸缎",
        props=["小灯笼", "祥云纹样摆件"], light="明亮暖光", mood="欢庆、大气、隆重（画面中不出现国旗、国徽）", bg="B8302A"),
    _fs("fest_autumn", "秋冬暖意", "焦糖针织 + 枫叶热饮，温暖舒适",
        backdrop="焦糖棕色素色背景", palette="焦糖棕、枫叶红、米白", surface="米白色针织毯",
        props=["枫叶", "热可可", "松果"], light="温暖的低角度斜阳", mood="温暖、舒适、秋冬", bg="A86A45"),
    _fs("fest_1111", "双11", "正红丝绒 + 珍珠烛台，大促仪式感",
        backdrop="正红色渐变背景，上深下浅", palette="正红、珊瑚粉", surface="红色丝绒台布",
        props=["珍珠串", "水晶玻璃烛台", "粉晶碎石"], light="暖色侧光", mood="热烈、隆重、仪式感", bg="B0262A"),
    _fs("fest_1212", "双12 年终", "暖红 + 礼盒针织，年终好礼",
        backdrop="暖红色素色背景", palette="暖红、米白", surface="米白色针织毯",
        props=["叠放的礼盒", "缎带", "纸质购物袋"], light="温暖柔光", mood="温暖、回馈、年终", bg="A8392E"),
    _fs("fest_dongzhi", "冬至", "米白暖调 + 汤圆饺子热茶，冬日暖意",
        backdrop="暖米白色背景，有热气氤氲", palette="米白、暖红", surface="浅色木桌",
        props=["一碗汤圆", "饺子", "热茶"], light="柔和暖光", mood="温暖、团圆、家常", bg="E6DACB"),
    _fs("fest_xmas", "圣诞复古", "墨绿 + 正红，扭扭棒圣诞树与人造雪",
        backdrop="墨绿色素色背景，带胶片颗粒质感", palette="墨绿、正红", surface="深红色台面，散落人造雪",
        props=["扭扭棒圣诞树", "红苹果", "人造雪"], light="柔和顶光，复古胶片感", mood="复古、童趣、温暖的圣诞", bg="1F5446"),
    _fs("fest_xmas_snow", "圣诞雪夜", "暖色串灯 + 花环松果，温馨圣诞夜",
        backdrop="深色背景，远处有虚化的暖黄色串灯光斑", palette="深棕、暖金、白", surface="铺着白雪的木台",
        props=["圣诞花环", "松果", "暖色串灯"], light="温暖的烛光与串灯", mood="温馨、静谧、节日", bg="2E2420"),
]

FESTIVAL_RAW = {
    "plant": """
red_berry|红果枝（南天竹）|红果,南天竹,冬青||m
red_plum|红梅枝|红梅,梅花||m
tulip|郁金香|郁金香||m
carnation|康乃馨|康乃馨||m
sprout|嫩绿新芽|嫩芽,新芽||s
mugwort|艾草菖蒲|艾草,菖蒲||m
""",
    "produce": """
mandarin|砂糖橘|砂糖橘,金桔,橘子||s
persimmon|柿子|柿子||s
pomelo|柚子|柚子||m
watermelon|西瓜切块|西瓜||s
chestnut|板栗|板栗,栗子||s
pumpkin|小南瓜|南瓜||s
tangyuan|一碗汤圆|汤圆||s
dumplings|饺子|饺子||s
zongzi|粽子|粽子||s
heart_choco|爱心巧克力|巧克力||s
candy|彩色糖果|糖果||s
lemonade|一杯柠檬水|柠檬水||m
""",
    "fabric": """
red_knit|红色粗针织毯|针织毯,毛毯||l
red_sweater|红色毛衣袖口|毛衣||m
cream_wool|米白色羊毛毯|羊毛||l
red_velvet|红色丝绒台布|红丝绒||l
picnic_cloth|格子野餐布|野餐布||l
red_gold_silk|红金色绸缎|绸缎||l
""",
    "vessel": """
red_arch|红色弧形纸艺展台|纸艺展台||l
wood_block|原木方块展台|木块||m
champagne|香槟杯|香槟||m
whisky_glass|威士忌杯|威士忌||s
hot_cocoa|一杯热可可|热可可,可可||s
hot_tea|一杯热茶|热茶||s
rattan_basket|藤编篮|藤编||m
crystal_holder|水晶玻璃烛台|烛台||m
canvas_bag|帆布包|帆布包||m
notebook|笔记本|笔记本||s
pencil|铅笔|铅笔||s
leather|皮革小物|皮革||s
watch|腕表一角|腕表,手表||s
moon_lamp|圆月灯|月亮灯||m
""",
    "natural": """
rose_quartz|粉晶碎石|粉晶||s
glass_bubble|玻璃气泡球|玻璃球||s
""",
    "light": """
string_lights|暖色串灯|串灯||l
star_lights|星星灯串|星星灯||l
palm_shadow|棕榈叶光影|棕榈||l
""",
    "festival": """
gold_ribbon|金色彩带|彩带||s
gold_sequin|金色亮片|亮片||s
sparkler|仙女棒烟花|仙女棒,烟花||m
gold_balloon|金色气球|金色气球||m
gift_stack|叠放的礼盒|礼盒||m
chinese_knot|中国结|中国结||m
red_string|红绳|红绳||s
fu_paper|福字窗花剪纸|窗花,剪纸||m
gold_ingot|金元宝摆件|元宝||s
firecracker|装饰鞭炮挂串|鞭炮||m
mahjong|麻将牌|麻将||s
flower_lantern|花灯|花灯||m
love_letter|情书信封|情书,信封||s
rose_bouquet|玫瑰花束|玫瑰花束||m
pearl|珍珠串|珍珠||s
crown|小皇冠摆件|皇冠||s
paper_butterfly|纸艺蝴蝶|蝴蝶||s
sunglasses|太阳镜|太阳镜,墨镜||s
travel_map|旅行地图|地图||m
film_camera|胶片相机|相机||s
greeting_card|手写卡片|贺卡,卡片||s
heart_balloon|爱心气球|爱心气球||m
shopping_bag|纸质购物袋|购物袋||m
balloon|彩色气球|气球||m
blocks|彩色积木|积木||s
plush_bear|毛绒小熊|小熊,玩偶||m
five_rope|五彩绳|五彩绳||s
sachet|香囊|香囊||s
dragon_boat|小龙舟摆件|龙舟||s
magpie_paper|喜鹊剪纸|喜鹊||s
tongxin_knot|红线同心结|同心结||s
rabbit|玉兔摆件|玉兔,兔子||s
xiangyun|祥云纹样摆件|祥云||s
pipe_tree|扭扭棒圣诞树|圣诞树||m
gingerbread|姜饼人|姜饼||s
bell|金色铃铛|铃铛||s
stocking|圣诞袜|圣诞袜||m
candy_cane|拐杖糖|拐杖糖||s
wreath|圣诞花环|花环||m
reindeer|驯鹿摆件|驯鹿||s
""",
}

FESTIVAL_PROMPTS = {
    "red_knit": "大片红色粗针织毛毯，自然堆叠出褶皱，针织纹理清晰",
    "red_arch": "红色哑光纸艺弧形拱桥展台，边缘干净",
    "fu_paper": "红色福字窗花剪纸，镂空花纹精致",
    "pipe_tree": "用绿色扭扭棒缠成的小圣诞树，点缀彩色小绒球",
    "sparkler": "点燃的仙女棒，金色火花虚化",
    "mahjong": "几张白绿配色的麻将牌，随意摆放",
    "string_lights": "背景里暖黄色串灯虚化成柔和光斑",
    "star_lights": "星星形状的小灯串，虚化成暖色光点",
    "moon_lamp": "一盏发出暖黄光的圆月灯",
    "gold_balloon": "几个金色气球（不带数字和文字）",
}
