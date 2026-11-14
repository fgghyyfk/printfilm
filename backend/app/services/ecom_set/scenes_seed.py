"""场景库首批系统场景（8 个行业，共 50 个）。启动时只插入缺失的 key，已有记录以管理后台为准。

字段见 scenes.normalize_spec；bg_color 是摆位底图的底色，尽量接近场景主色。
"""

from __future__ import annotations


def _s(key: str, name: str, industries: list[str], desc: str, *, backdrop: str, palette: str, surface: str,
       props: list[str], light: str, mood: str, bg: str, ratio: float = 0.5, blank: str = "top") -> dict:
    """组装一条系统场景。"""
    return {
        "key": key, "name": name, "industries": industries, "description": desc,
        "spec": {"backdrop": backdrop, "palette": palette, "surface": surface, "props": props, "light": light,
                 "mood": mood, "bg_color": bg, "product_ratio": ratio, "blank": blank},
    }


SYSTEM_SCENES: list[dict] = [
    # ---------- 日化个护（护手霜、身体乳、洗护、香薰） ----------
    _s("daily_wood_stone", "自然原木石", ["daily", "beauty"], "素色背景 + 洞石台 + 老木松针，温润高级",
       backdrop="干净的素色暖米驼色背景墙", palette="暖米驼、浅棕", surface="平整的浅米色天然洞石台",
       props=["粗糙小岩石", "深棕色老木块", "松针", "细草叶", "半片新鲜橙子", "一小截肉桂"],
       light="柔和窗光从左上方照来", mood="安静、温润、高级", bg="D8C3A8"),
    _s("daily_plant_morning", "植物晨光", ["daily", "beauty"], "奶油白墙 + 原木 + 绿植叶影",
       backdrop="奶油白色墙面，墙上有窗外植物投下的斜向叶影", palette="奶油白、浅木色、嫩绿", surface="浅色原木块",
       props=["龟背竹叶片", "尤加利枝", "白色鹅卵石"], light="清晨暖色窗光从右侧照来，带树叶光影",
       mood="清新、自然、松弛", bg="EFE7DA"),
    _s("daily_citrus", "柑橘清新", ["daily", "beauty", "food"], "浅杏色 + 亚麻布 + 柑橘薄荷",
       backdrop="浅杏色素色背景", palette="浅杏、橙黄、薄荷绿", surface="米白色亚麻布",
       props=["橙子切片", "葡萄柚切片", "薄荷叶"], light="明亮的自然侧光", mood="清爽、活力、元气", bg="F1DFC8"),
    _s("daily_water", "浴室水感", ["daily", "beauty"], "雾蓝 + 水面波纹 + 水花鹅卵石",
       backdrop="雾霾蓝渐变背景", palette="雾霾蓝、米白", surface="平静的浅水面，有柔和波纹",
       props=["溅起的细小水花", "水珠", "白色鹅卵石"], light="逆光通透，水珠闪亮", mood="清透、干净、水润", bg="C9D6DD"),
    _s("daily_floral", "花香柔美", ["daily", "beauty"], "浅粉奶油 + 大理石 + 花瓣丝绸",
       backdrop="浅粉色奶油调素色背景", palette="浅粉、奶油白", surface="白色大理石台面",
       props=["散落的玫瑰花瓣", "一枝牡丹", "柔软的米白丝绸"], light="柔和散射光", mood="温柔、浪漫、精致", bg="EBD6D3"),
    _s("daily_holiday", "节日礼盒", ["daily", "beauty", "food", "liquor"], "酒红暖金 + 松枝肉桂，节日送礼",
       backdrop="深酒红色背景，远处有虚化的暖金色光斑", palette="酒红、暖金", surface="深色木台",
       props=["松枝", "肉桂棒", "干橙片", "金色缎带"], light="温暖的侧光", mood="喜庆、温暖、适合送礼", bg="7A2E2A"),
    _s("daily_minimal_white", "极简白调", ["daily", "beauty", "digital"], "纯白渐变 + 亚克力台阶，干净主图风",
       backdrop="纯白到浅灰的柔和渐变背景", palette="白、浅灰", surface="白色亚克力几何台阶",
       props=["一枝细长绿植"], light="棚拍大柔光，阴影很浅", mood="干净、专业、极简", bg="F2F2F0", ratio=0.55),

    # ---------- 美妆护肤 ----------
    _s("beauty_serum_water", "水润精华", ["beauty"], "水波纹 + 水滴 + 玻璃折射，冷调清透",
       backdrop="浅冰蓝色背景，有水波纹光影", palette="冰蓝、透明", surface="浅水面，表面有同心圆波纹",
       props=["水滴", "透明玻璃球"], light="逆光与顶光，水滴晶莹", mood="清透、水润、科技感", bg="CFE0E6"),
    _s("beauty_marble", "高级大理石", ["beauty", "jewelry"], "黑白大理石 + 金属托盘 + 硬光投影",
       backdrop="深灰色素色背景", palette="黑、白、香槟金", surface="黑白纹理大理石台面",
       props=["香槟金色小托盘", "一枝干花"], light="侧上方硬光，投下清晰的几何阴影", mood="高级、克制、奢华", bg="5A5652"),
    _s("beauty_ingredient", "成分可视化", ["beauty", "daily"], "道具用核心成分原料，仅作氛围示意",
       backdrop="浅绿色素色背景", palette="浅绿、米白", surface="浅色原木台",
       props=["新鲜绿叶", "植物原料", "透明水珠"], light="柔和自然光", mood="天然、纯净、植萃", bg="DDE5D3"),
    _s("beauty_color_block", "撞色潮流", ["beauty", "food"], "高饱和撞色背景，适合彩妆与年轻品牌",
       backdrop="高饱和纯色背景（与商品主色形成撞色）", palette="高饱和撞色", surface="同色系几何台",
       props=["几何色块"], light="明亮硬光，投影清晰", mood="潮流、活力、年轻", bg="E9B44C"),
    _s("beauty_vanity", "梳妆台生活感", ["beauty", "daily"], "梳妆台 + 化妆镜 + 清晨窗光",
       backdrop="虚化的卧室梳妆台一角，暖白墙面", palette="暖白、浅木色", surface="浅色木质梳妆台面",
       props=["圆形化妆镜一角", "发夹", "米白亚麻布"], light="清晨窗光", mood="真实、生活化、温柔", bg="E8DED2", ratio=0.45),
    _s("beauty_acrylic", "极简亚克力", ["beauty", "digital"], "几何亚克力台阶 + 纯色渐变",
       backdrop="柔和的粉灰渐变背景", palette="粉灰、透明", surface="透明亚克力几何台阶",
       props=["半透明亚克力圆片"], light="柔光，带透明折射", mood="现代、简洁、轻盈", bg="E4D9D6"),

    # ---------- 食品零食 ----------
    _s("food_origin", "原料溯源", ["food"], "产品周围散落原料，木桌俯拍",
       backdrop="深色原木桌面（俯视角）", palette="原木棕、原料本色", surface="深色原木桌",
       props=["散落的原料（坚果、谷物或果干，按商品实际原料）", "小木勺", "麻布"],
       light="柔和侧光", mood="天然、真材实料", bg="8A6B50", blank="none"),
    _s("food_breakfast", "早餐餐桌", ["food"], "晨光餐桌，牛奶杯与餐盘",
       backdrop="虚化的早餐餐桌与窗户", palette="暖白、浅木色", surface="浅色木质餐桌",
       props=["一杯牛奶", "小餐盘", "几颗新鲜浆果"], light="清晨暖色窗光", mood="温馨、健康、元气", bg="EADFCF", ratio=0.45),
    _s("food_picnic", "野餐户外", ["food"], "格子布 + 草地虚化 + 阳光",
       backdrop="虚化的草地与树荫", palette="嫩绿、红白格", surface="红白格子野餐布",
       props=["小藤编篮", "新鲜水果"], light="明亮的午后阳光", mood="轻松、快乐、户外", bg="BFD3A5", ratio=0.45),
    _s("food_office", "办公室下午茶", ["food"], "键盘咖啡杯虚化，轻松一刻",
       backdrop="虚化的办公桌与电脑", palette="浅灰、暖白", surface="浅色办公桌面",
       props=["一杯咖啡", "便签本"], light="柔和室内光", mood="轻松、解馋、午后", bg="DCD8D2", ratio=0.45),
    _s("food_pop", "撞色潮流零食", ["food"], "高饱和纯色 + 产品悬浮感",
       backdrop="高饱和纯色背景", palette="高饱和撞色", surface="同色系圆形展台",
       props=["散落的零食颗粒"], light="明亮硬光", mood="潮流、好玩、年轻", bg="F0A04B"),
    _s("food_festival", "年货节", ["food", "tea", "liquor"], "红金喜庆，年货送礼",
       backdrop="喜庆的中国红背景，有虚化的灯笼光斑", palette="中国红、金", surface="深红色锦缎台面",
       props=["红色礼盒一角", "金色福字小挂件（无文字可辨）", "坚果果盘"], light="温暖的侧光", mood="喜庆、团圆、送礼", bg="A3221F"),

    # ---------- 茶叶 ----------
    _s("tea_zen", "禅意茶席", ["tea"], "竹帘茶盏，素色宣纸背景",
       backdrop="素色宣纸质感背景，远处有竹帘虚影", palette="米白、浅木、青灰", surface="深色老木茶台",
       props=["小茶盏", "散落的干茶叶", "一块素石"], light="侧光，带竹帘光影", mood="安静、禅意、雅致", bg="E6E0D3"),
    _s("tea_mountain", "茶山原产", ["tea"], "茶园雾气，嫩芽特写",
       backdrop="虚化的茶园山坡，有薄雾", palette="青绿、雾白", surface="带苔藓的山石",
       props=["新鲜茶叶嫩芽", "露水"], light="清晨薄雾中的柔光", mood="原产、鲜爽、自然", bg="BFCDBB"),
    _s("tea_new_chinese", "新中式", ["tea", "liquor"], "木格窗影 + 青瓷 + 枯枝",
       backdrop="浅色墙面，有木格窗投下的光影", palette="米白、青瓷绿、深木色", surface="深色胡桃木台",
       props=["青瓷小杯", "一枝枯枝"], light="斜射暖光，窗格光影清晰", mood="东方、雅致、有格调", bg="DCD3C4"),
    _s("tea_brew", "冲泡特写", ["tea"], "玻璃杯茶汤与蒸汽，逆光",
       backdrop="暖灰色素色背景", palette="琥珀、暖灰", surface="浅色石板",
       props=["透明玻璃杯中的茶汤", "升起的热气", "几片舒展的茶叶"], light="逆光，茶汤透亮", mood="温暖、醇香", bg="CBBFB0"),
    _s("tea_gift", "茶礼", ["tea"], "锦盒与丝带，送礼体面",
       backdrop="墨绿色素色背景", palette="墨绿、金", surface="深色木台",
       props=["锦缎布", "金色丝带", "小茶罐"], light="柔和侧光", mood="体面、讲究、送礼", bg="2F4A3E"),
    _s("tea_leaf_flat", "原叶平铺", ["tea"], "俯拍散落茶叶与竹器",
       backdrop="浅色竹编背景（俯视角）", palette="竹黄、茶绿", surface="竹编簸箕",
       props=["散落的干茶叶", "小竹勺"], light="均匀柔光", mood="原叶、手作、真实", bg="D8C59F", blank="none"),

    # ---------- 酒类 ----------
    _s("liquor_banquet", "东方宴席", ["liquor"], "深色木桌 + 酒盅 + 灯笼虚化（白酒）",
       backdrop="虚化的中式宴席与红灯笼光斑", palette="深红、暗金、深木色", surface="深色红木桌",
       props=["白瓷小酒盅", "一枝梅花"], light="温暖的侧光", mood="隆重、东方、宴请", bg="4A2620", ratio=0.55),
    _s("liquor_cellar", "酒窖橡木", ["liquor"], "橡木桶 + 暗调硬光（红酒/威士忌）",
       backdrop="昏暗的酒窖，有虚化的橡木桶", palette="深棕、琥珀", surface="旧橡木桶顶",
       props=["一只高脚杯", "软木塞"], light="一束暖色硬光从侧上方照来", mood="醇厚、复古、有年份感", bg="3A2A20", ratio=0.55),
    _s("liquor_pairing", "餐酒搭配", ["liquor"], "奶酪葡萄与餐刀，暖光",
       backdrop="虚化的餐厅暖色背景", palette="暖棕、酒红", surface="原木砧板",
       props=["几块奶酪", "一串葡萄", "餐刀"], light="温暖的侧光", mood="惬意、品味、分享", bg="6E4B3A", ratio=0.5),
    _s("liquor_ice", "冰镇清爽", ["liquor", "food"], "冰块水珠 + 冷色逆光（精酿/果酒）",
       backdrop="冷蓝色渐变背景", palette="冰蓝、透明", surface="湿润的深色石板",
       props=["碎冰块", "水珠", "柠檬片"], light="冷色逆光，水珠晶莹", mood="冰爽、畅快、夏日", bg="2E4A5E"),
    _s("liquor_luxury", "礼赠高端", ["liquor", "tea", "jewelry"], "黑金背景 + 丝绸，高端礼赠",
       backdrop="黑色背景，有细微的金色光斑", palette="黑、金", surface="黑色镜面台",
       props=["黑色丝绸", "金色缎带"], light="顶部聚光，轮廓光勾边", mood="尊贵、高端、礼赠", bg="1E1C1A", ratio=0.55),
    _s("liquor_new_chinese", "国潮山水", ["liquor", "tea"], "水墨山水虚化 + 石台",
       backdrop="淡雅水墨山水意境的虚化背景", palette="墨黑、米白、朱红点缀", surface="天然青石台",
       props=["一枝松枝", "小块太湖石"], light="柔和侧光", mood="国潮、意境、文化感", bg="D9D5CC"),

    # ---------- 3C 数码 ----------
    _s("digital_dark_tech", "科技暗调", ["digital"], "深色渐变 + 冷色轮廓光 + 反光台面",
       backdrop="深蓝灰渐变背景", palette="深空灰、冷蓝", surface="带倒影的黑色亮面台",
       props=["细线条光带"], light="冷色轮廓光勾边，顶光柔和", mood="科技、专业、高性能", bg="1F2730"),
    _s("digital_white", "极简白主图", ["digital"], "白色无缝 + 柔光，平台主图规范",
       backdrop="纯白无缝背景", palette="白", surface="白色无缝台面",
       props=[], light="棚拍大柔光，浅接地阴影", mood="干净、标准、清晰", bg="F5F5F5", ratio=0.6),
    _s("digital_desk", "桌面工作流", ["digital"], "显示器键盘绿植，生活场景",
       backdrop="虚化的书桌、显示器与绿植", palette="浅木色、白、绿", surface="浅色木质书桌",
       props=["键盘一角", "一盆小绿植", "咖啡杯"], light="明亮的自然窗光", mood="高效、舒适、生活化", bg="DDD5C8", ratio=0.45),
    _s("digital_outdoor", "户外运动", ["digital"], "跑道山野虚化（耳机、手表）",
       backdrop="虚化的户外山野或跑道", palette="天蓝、草绿", surface="粗糙的户外岩石",
       props=["运动毛巾一角"], light="明亮的户外阳光", mood="自由、运动、活力", bg="9FB7C9", ratio=0.45),
    _s("digital_geometry", "几何展台", ["digital", "beauty"], "彩色几何体 + 撞色（潮流配件）",
       backdrop="柔和撞色背景", palette="马卡龙撞色", surface="彩色几何体展台（圆柱、方块）",
       props=["小几何体"], light="明亮柔光", mood="潮流、好玩、设计感", bg="C9B8E0"),
    _s("digital_neon", "霓虹夜色", ["digital"], "暗色背景 + 霓虹光（电竞、潮玩）",
       backdrop="暗色背景，带紫蓝霓虹光晕", palette="紫、蓝、品红", surface="深色亮面台",
       props=["霓虹灯条反光"], light="霓虹侧光，色彩反射", mood="电竞、酷炫、夜色", bg="1C1530"),

    # ---------- 珠宝首饰（单品展示，非佩戴） ----------
    _s("jewelry_velvet", "丝绒展台", ["jewelry"], "深色丝绒 + 聚光 + 闪耀高光",
       backdrop="深墨绿色丝绒背景", palette="墨绿、金", surface="深色丝绒展台",
       props=[], light="顶部小范围聚光，金属高光闪耀", mood="珍贵、经典、高级", bg="1F3A33", ratio=0.4),
    _s("jewelry_stone", "自然石材", ["jewelry"], "原石贝壳沙面（珍珠、银饰）",
       backdrop="浅米色素色背景", palette="米白、沙色", surface="细腻的浅色沙面",
       props=["一块粗糙原石", "小贝壳"], light="柔和侧光", mood="自然、温润、海风感", bg="E3D7C5", ratio=0.4),
    _s("jewelry_floral", "花材柔美", ["jewelry"], "花瓣丝绸柔光（婚嫁、礼物）",
       backdrop="浅粉色柔和背景", palette="浅粉、珍珠白", surface="米白色丝绸",
       props=["散落的玫瑰花瓣", "一朵小白花"], light="柔和散射光", mood="浪漫、甜美、婚嫁", bg="EED9D6", ratio=0.4),
    _s("jewelry_black_gold", "黑金高端", ["jewelry"], "黑色镜面 + 金色光斑",
       backdrop="黑色背景，有虚化的金色光斑", palette="黑、金", surface="黑色镜面台，有清晰倒影",
       props=[], light="一束硬光打在首饰上，强烈高光", mood="奢华、闪耀、高端", bg="141414", ratio=0.4),
    _s("jewelry_plaster", "石膏几何", ["jewelry", "beauty"], "白色石膏几何体 + 柔光，现代简约",
       backdrop="暖白色素色背景", palette="暖白、浅灰", surface="白色石膏几何体（圆柱、拱形）",
       props=["一枝干花"], light="柔和侧光，阴影干净", mood="现代、简约、设计感", bg="EDE8E1", ratio=0.4),
    _s("jewelry_water", "水光潋滟", ["jewelry"], "水面与光斑（银饰、钻石）",
       backdrop="浅蓝色背景，有水面反射的光斑", palette="浅蓝、银白", surface="浅水面",
       props=["细小水珠"], light="顶光透过水面形成光斑", mood="灵动、清透、闪耀", bg="CADAE2", ratio=0.4),

    # ---------- 五金工具（电钻、起子机、角磨、手动工具） ----------
    _s("hardware_dark_steel", "工业暗调", ["hardware"], "深灰钢板 + 冷暖轮廓光，硬朗有力量",
       backdrop="深炭灰色金属质感背景，远处有虚化的暖色光斑", palette="炭灰、钢灰、少量暖橙", surface="磨砂深灰钢板台面",
       props=["几颗散落的六角螺栓", "一小撮金属屑"], light="冷色轮廓光勾边，暖色侧光打亮机身", mood="硬朗、专业、有力量",
       bg="2B2E33", ratio=0.42),
    _s("hardware_workbench", "木作工作台", ["hardware"], "实木工作台 + 洞洞板工具墙，手作感",
       backdrop="虚化的洞洞板工具墙，挂着几件手动工具", palette="原木色、墨绿、浅灰", surface="有使用痕迹的实木工作台",
       props=["几颗木螺丝", "一卷卷尺", "少量刨花木屑"], light="明亮的自然窗光从左侧照来", mood="踏实、手作、生活化",
       bg="CDB89A", ratio=0.42),
    _s("hardware_renovation", "家装现场", ["hardware"], "白墙水泥地 + 梯子，装修进行时",
       backdrop="虚化的装修中房间，白墙与木梯", palette="暖白、水泥灰、原木", surface="浅灰色水泥地面",
       props=["一块木板", "几颗膨胀螺丝"], light="明亮的室内自然光", mood="真实、能干、居家", bg="D9D5CE", ratio=0.42),
    _s("hardware_concrete", "水泥质感", ["hardware"], "清水混凝土 + 顶光，简洁工业风",
       backdrop="清水混凝土墙面", palette="水泥灰、黑", surface="粗糙的水泥台面",
       props=[], light="顶部柔和硬光，阴影利落", mood="简洁、工业、耐用", bg="A7A49F", ratio=0.45),
    _s("hardware_garage", "车库工坊", ["hardware"], "车库工具墙 + 暖光，DIY 氛围",
       backdrop="虚化的车库工坊，墙上挂着工具，暖色吊灯", palette="暖橙、深灰、木色", surface="深色金属工作台",
       props=["一盒钻头", "手套"], light="暖色吊灯顶光，背景有柔和光斑", mood="DIY、热爱、男人的空间", bg="5A4A3C", ratio=0.42),

    # ---------- 通用 ----------
    _s("common_studio_gray", "影棚灰", ["daily", "beauty", "food", "digital", "jewelry", "hardware"], "中性灰渐变，什么商品都不出错",
       backdrop="中性灰渐变背景", palette="中性灰", surface="浅灰色无缝台面",
       props=[], light="棚拍柔光，柔和接地阴影", mood="专业、中性、百搭", bg="C8C6C2", ratio=0.55),
    _s("common_sunlit_window", "午后窗影", ["daily", "beauty", "food", "tea"], "素墙 + 斜射窗影，自然高级",
       backdrop="素色暖白墙面，有午后阳光透过窗户投下的斜向光影", palette="暖白、金色阳光", surface="浅色石台",
       props=["一枝绿植"], light="午后斜射阳光，光影分明", mood="温暖、自然、高级", bg="E9DFD0"),
]

# 场景风格分组（节日场景都归「节日」）：给用户按风格快速筛选
SCENE_GROUPS: dict[str, str] = {
    "festival": "节日氛围",
    "natural": "自然质感",
    "fresh": "清新植物",
    "floral": "花香柔美",
    "water": "水感清透",
    "minimal": "极简棚拍",
    "luxury": "高级质感",
    "oriental": "东方新中式",
    "life": "生活场景",
    "pop": "潮流撞色",
}
_GROUP_MEMBERS = {
    "natural": "daily_wood_stone food_origin jewelry_stone tea_mountain",
    "fresh": "daily_plant_morning daily_citrus beauty_ingredient common_sunlit_window",
    "floral": "daily_floral jewelry_floral",
    "water": "daily_water beauty_serum_water jewelry_water liquor_ice",
    "minimal": "hardware_concrete daily_minimal_white beauty_acrylic digital_white common_studio_gray jewelry_plaster digital_geometry",
    "luxury": "hardware_dark_steel beauty_marble jewelry_velvet jewelry_black_gold liquor_luxury liquor_cellar digital_dark_tech",
    "oriental": "tea_zen tea_new_chinese tea_gift tea_leaf_flat liquor_banquet liquor_new_chinese",
    "life": "hardware_workbench hardware_renovation hardware_garage beauty_vanity food_breakfast food_picnic food_office digital_desk digital_outdoor liquor_pairing tea_brew",
    "pop": "beauty_color_block food_pop digital_neon",
    "festival": "daily_holiday food_festival",
}
SCENE_GROUP_OF: dict[str, str] = {k: g for g, keys in _GROUP_MEMBERS.items() for k in keys.split()}


def group_of(key: str) -> str:
    """场景所属风格组；节日场景（fest_ 开头）归节日，其余未登记的（我的场景等）归空。"""
    return "festival" if key.startswith("fest_") else SCENE_GROUP_OF.get(key, "")
