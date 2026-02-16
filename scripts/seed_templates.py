"""Seed script: Insert 50 prompt templates (5 per category) into the database."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta, timezone  # noqa: E402
from uuid import uuid4  # noqa: E402

from app.db import async_session_factory  # noqa: E402
from app.models.prompt_template import PromptTemplate  # noqa: E402


def compute_trending(like: int, use: int, fav: int, hours_ago: float) -> float:
    return (like * 3 + use * 1 + fav * 2) / (hours_ago + 2) ** 1.5


TEMPLATES: list[dict[str, str | list[str] | int]] = [
    # ── portrait (5) ──
    {
        "prompt_text": "A cinematic portrait of a young woman with golden hour lighting, "
        "soft bokeh background, Hasselblad medium format look, film grain, "
        "warm color grading, shallow depth of field, editorial fashion photography",
        "display_name_en": "Golden Hour Portrait",
        "display_name_zh": "黄金时刻人像",
        "description_en": "Cinematic portrait with warm golden hour lighting and film-like quality",
        "description_zh": "带有温暖黄金时刻光线和电影质感的电影级人像",
        "category": "portrait",
        "tags": ["portrait", "golden-hour", "cinematic", "fashion"],
        "style_keywords": ["cinematic", "Hasselblad", "film grain"],
        "difficulty": "beginner",
        "use_count": 1250,
        "like_count": 340,
        "favorite_count": 180,
    },
    {
        "prompt_text": "Close-up portrait of an elderly man with deep wrinkles and wise eyes, "
        "Rembrandt lighting, dramatic chiaroscuro, black and white, "
        "high contrast, studio photography, 85mm f/1.4",
        "display_name_en": "Dramatic B&W Portrait",
        "display_name_zh": "黑白戏剧人像",
        "description_en": "High-contrast black and white portrait with classic Rembrandt lighting",
        "description_zh": "经典伦勃朗光效的高对比度黑白人像",
        "category": "portrait",
        "tags": ["portrait", "black-and-white", "dramatic", "studio"],
        "style_keywords": ["Rembrandt lighting", "chiaroscuro", "high contrast"],
        "difficulty": "intermediate",
        "use_count": 890,
        "like_count": 256,
        "favorite_count": 120,
    },
    {
        "prompt_text": "Environmental portrait of a street musician playing violin in a rainy alley, "
        "neon reflections on wet pavement, cyberpunk atmosphere, "
        "moody teal and orange color palette, 35mm street photography",
        "display_name_en": "Neon Street Portrait",
        "display_name_zh": "霓虹街头人像",
        "description_en": "Moody street portrait with neon-lit cyberpunk atmosphere",
        "description_zh": "霓虹灯下赛博朋克氛围的情绪街头人像",
        "category": "portrait",
        "tags": ["portrait", "street", "cyberpunk", "neon"],
        "style_keywords": ["cyberpunk", "neon", "teal and orange"],
        "difficulty": "intermediate",
        "use_count": 670,
        "like_count": 198,
        "favorite_count": 95,
    },
    {
        "prompt_text": "Ethereal double exposure portrait merging a woman's face with a forest landscape, "
        "misty morning light, dreamy atmosphere, soft pastel tones, "
        "fine art photography, surrealist composition",
        "display_name_en": "Double Exposure Forest",
        "display_name_zh": "森林双重曝光",
        "description_en": "Surrealist double exposure blending portrait with nature",
        "description_zh": "人像与自然融合的超现实双重曝光",
        "category": "portrait",
        "tags": ["portrait", "double-exposure", "surreal", "nature"],
        "style_keywords": ["double exposure", "surrealist", "fine art"],
        "difficulty": "advanced",
        "use_count": 420,
        "like_count": 165,
        "favorite_count": 88,
    },
    {
        "prompt_text": "Clean beauty portrait with flawless skin, studio ring light, "
        "white background, minimalist aesthetic, high-key lighting, "
        "magazine cover quality, 100mm macro lens detail",
        "display_name_en": "Beauty Studio Shot",
        "display_name_zh": "美妆棚拍",
        "description_en": "Clean high-key beauty portrait perfect for commercial use",
        "description_zh": "简洁高调美妆人像，适合商业用途",
        "category": "portrait",
        "tags": ["portrait", "beauty", "studio", "commercial"],
        "style_keywords": ["high-key", "minimalist", "ring light"],
        "difficulty": "beginner",
        "use_count": 980,
        "like_count": 210,
        "favorite_count": 145,
    },
    # ── landscape (5) ──
    {
        "prompt_text": "Majestic mountain landscape at sunrise with dramatic cloud formations, "
        "alpine lake reflection, golden light rays piercing through peaks, "
        "National Geographic quality, ultra wide angle, 4K detail",
        "display_name_en": "Alpine Sunrise",
        "display_name_zh": "高山日出",
        "description_en": "Breathtaking mountain sunrise with mirror-like lake reflection",
        "description_zh": "令人惊叹的山间日出与镜面湖泊倒影",
        "category": "landscape",
        "tags": ["landscape", "mountain", "sunrise", "nature"],
        "style_keywords": ["National Geographic", "ultra wide angle", "dramatic"],
        "difficulty": "beginner",
        "use_count": 1580,
        "like_count": 420,
        "favorite_count": 230,
    },
    {
        "prompt_text": "Aerial view of turquoise ocean waves crashing on white sand beach, "
        "drone photography, tropical paradise, coral reef visible underwater, "
        "vivid colors, top-down perspective, summer vibes",
        "display_name_en": "Tropical Aerial Beach",
        "display_name_zh": "热带航拍海滩",
        "description_en": "Stunning drone shot of a tropical beach with crystal clear water",
        "description_zh": "热带海滩清澈海水的惊艳航拍照",
        "category": "landscape",
        "tags": ["landscape", "beach", "aerial", "tropical"],
        "style_keywords": ["drone photography", "aerial", "vivid colors"],
        "difficulty": "beginner",
        "use_count": 1120,
        "like_count": 310,
        "favorite_count": 175,
    },
    {
        "prompt_text": "Misty bamboo forest path in Kyoto Japan, morning fog, "
        "zen atmosphere, soft diffused light, leading lines, "
        "wabi-sabi aesthetic, peaceful meditation scene",
        "display_name_en": "Zen Bamboo Forest",
        "display_name_zh": "禅意竹林",
        "description_en": "Serene Japanese bamboo forest path shrouded in morning mist",
        "description_zh": "晨雾缭绕的宁静日式竹林小径",
        "category": "landscape",
        "tags": ["landscape", "japan", "zen", "forest"],
        "style_keywords": ["wabi-sabi", "zen", "misty"],
        "difficulty": "beginner",
        "use_count": 860,
        "like_count": 275,
        "favorite_count": 150,
    },
    {
        "prompt_text": "Northern lights aurora borealis over a frozen lake in Iceland, "
        "vibrant green and purple curtains of light, starry sky, "
        "long exposure, reflection on ice, astrophotography",
        "display_name_en": "Aurora Over Ice",
        "display_name_zh": "冰面极光",
        "description_en": "Spectacular northern lights reflected on a frozen Icelandic lake",
        "description_zh": "冰岛冰冻湖面上壮观的北极光倒影",
        "category": "landscape",
        "tags": ["landscape", "aurora", "iceland", "night"],
        "style_keywords": ["astrophotography", "long exposure", "vibrant"],
        "difficulty": "intermediate",
        "use_count": 720,
        "like_count": 289,
        "favorite_count": 160,
    },
    {
        "prompt_text": "Vast desert sand dunes at golden hour, sweeping curves and shadows, "
        "minimalist composition, warm amber tones, Sahara desert, "
        "sense of scale with tiny human figure, epic landscape",
        "display_name_en": "Desert Dunes Minimal",
        "display_name_zh": "极简沙丘",
        "description_en": "Minimalist desert landscape with dramatic light and shadow play",
        "description_zh": "戏剧性光影的极简沙漠风景",
        "category": "landscape",
        "tags": ["landscape", "desert", "minimal", "golden-hour"],
        "style_keywords": ["minimalist", "golden hour", "epic"],
        "difficulty": "beginner",
        "use_count": 540,
        "like_count": 178,
        "favorite_count": 92,
    },
    # ── illustration (5) ──
    {
        "prompt_text": "Whimsical children's book illustration of a fox reading a book under a mushroom, "
        "watercolor style, soft pastel colors, storybook aesthetic, "
        "hand-painted texture, cozy woodland scene",
        "display_name_en": "Storybook Fox",
        "display_name_zh": "故事书小狐狸",
        "description_en": "Charming watercolor illustration perfect for children's books",
        "description_zh": "迷人的水彩插画，适合儿童读物",
        "category": "illustration",
        "tags": ["illustration", "watercolor", "children", "animal"],
        "style_keywords": ["watercolor", "storybook", "pastel"],
        "difficulty": "beginner",
        "use_count": 920,
        "like_count": 310,
        "favorite_count": 190,
    },
    {
        "prompt_text": "Detailed botanical illustration of exotic orchids, scientific accuracy, "
        "vintage engraving style, aged paper texture, hand-drawn ink lines, "
        "natural history museum quality, labeled diagram",
        "display_name_en": "Botanical Orchid Study",
        "display_name_zh": "兰花植物图鉴",
        "description_en": "Scientific botanical illustration in vintage engraving style",
        "description_zh": "复古雕版风格的科学植物插画",
        "category": "illustration",
        "tags": ["illustration", "botanical", "vintage", "scientific"],
        "style_keywords": ["engraving", "vintage", "scientific"],
        "difficulty": "intermediate",
        "use_count": 560,
        "like_count": 189,
        "favorite_count": 110,
    },
    {
        "prompt_text": "Isometric pixel art illustration of a cozy coffee shop interior, "
        "retro gaming aesthetic, warm indoor lighting, tiny detailed characters, "
        "16-bit color palette, nostalgic vibe",
        "display_name_en": "Pixel Coffee Shop",
        "display_name_zh": "像素咖啡店",
        "description_en": "Charming isometric pixel art of a cozy cafe scene",
        "description_zh": "温馨等轴像素咖啡馆场景",
        "category": "illustration",
        "tags": ["illustration", "pixel-art", "isometric", "retro"],
        "style_keywords": ["pixel art", "isometric", "16-bit"],
        "difficulty": "intermediate",
        "use_count": 780,
        "like_count": 245,
        "favorite_count": 135,
    },
    {
        "prompt_text": "Art nouveau poster illustration of a woman surrounded by flowing flowers, "
        "Alphonse Mucha inspired, ornate decorative borders, "
        "rich jewel tones, elegant typography space, vintage poster art",
        "display_name_en": "Art Nouveau Poster",
        "display_name_zh": "新艺术海报",
        "description_en": "Elegant Art Nouveau poster in the style of Alphonse Mucha",
        "description_zh": "穆夏风格的优雅新艺术运动海报",
        "category": "illustration",
        "tags": ["illustration", "art-nouveau", "poster", "vintage"],
        "style_keywords": ["Art Nouveau", "Mucha", "ornate"],
        "difficulty": "advanced",
        "use_count": 430,
        "like_count": 178,
        "favorite_count": 95,
    },
    {
        "prompt_text": "Cute kawaii illustration of animals having a tea party in a garden, "
        "flat design style, bright cheerful colors, round shapes, "
        "Japanese kawaii aesthetic, sticker-ready, clean vector look",
        "display_name_en": "Kawaii Tea Party",
        "display_name_zh": "卡哇伊茶会",
        "description_en": "Adorable kawaii-style animal tea party illustration",
        "description_zh": "可爱卡哇伊风格的动物茶会插画",
        "category": "illustration",
        "tags": ["illustration", "kawaii", "cute", "flat-design"],
        "style_keywords": ["kawaii", "flat design", "vector"],
        "difficulty": "beginner",
        "use_count": 1100,
        "like_count": 380,
        "favorite_count": 210,
    },
    # ── product (5) ──
    {
        "prompt_text": "Luxury perfume bottle product shot on marble surface, "
        "soft studio lighting, water droplets, golden reflections, "
        "commercial advertising quality, minimalist background, "
        "high-end beauty product photography",
        "display_name_en": "Luxury Perfume Shot",
        "display_name_zh": "奢侈香水产品照",
        "description_en": "Premium product photography for luxury perfume advertising",
        "description_zh": "奢侈香水广告的高端产品摄影",
        "category": "product",
        "tags": ["product", "luxury", "perfume", "commercial"],
        "style_keywords": ["commercial", "luxury", "minimalist"],
        "difficulty": "beginner",
        "use_count": 890,
        "like_count": 230,
        "favorite_count": 145,
    },
    {
        "prompt_text": "Floating sneaker product shot with dynamic splash of colorful paint, "
        "black background, dramatic rim lighting, levitation effect, "
        "sports brand advertising style, energetic and bold",
        "display_name_en": "Dynamic Sneaker Splash",
        "display_name_zh": "动感球鞋飞溅",
        "description_en": "Eye-catching sneaker shot with paint splash effects",
        "description_zh": "带有油漆飞溅效果的吸睛球鞋照",
        "category": "product",
        "tags": ["product", "sneaker", "dynamic", "sports"],
        "style_keywords": ["levitation", "splash", "dramatic lighting"],
        "difficulty": "intermediate",
        "use_count": 670,
        "like_count": 198,
        "favorite_count": 105,
    },
    {
        "prompt_text": "Artisan coffee bag packaging mockup on rustic wooden table, "
        "morning sunlight, steam rising from cup nearby, "
        "lifestyle product photography, warm earth tones, "
        "craft brand aesthetic, natural setting",
        "display_name_en": "Artisan Coffee Lifestyle",
        "display_name_zh": "手工咖啡生活照",
        "description_en": "Warm lifestyle product shot for artisan coffee branding",
        "description_zh": "手工咖啡品牌的温暖生活方式产品照",
        "category": "product",
        "tags": ["product", "coffee", "lifestyle", "packaging"],
        "style_keywords": ["lifestyle", "rustic", "natural"],
        "difficulty": "beginner",
        "use_count": 750,
        "like_count": 210,
        "favorite_count": 125,
    },
    {
        "prompt_text": "Sleek smartphone floating in space with holographic UI elements, "
        "futuristic tech product visualization, glowing edges, "
        "dark background with particle effects, sci-fi aesthetic, "
        "next-gen technology concept",
        "display_name_en": "Futuristic Phone Concept",
        "display_name_zh": "未来手机概念",
        "description_en": "Sci-fi inspired smartphone visualization with holographic elements",
        "description_zh": "全息元素的科幻风智能手机可视化",
        "category": "product",
        "tags": ["product", "tech", "futuristic", "concept"],
        "style_keywords": ["futuristic", "holographic", "sci-fi"],
        "difficulty": "advanced",
        "use_count": 350,
        "like_count": 145,
        "favorite_count": 78,
    },
    {
        "prompt_text": "Flat lay product arrangement of skincare routine items, "
        "clean white marble background, soft shadows, "
        "organized layout, pastel packaging, top-down view, "
        "Instagram-worthy aesthetic, wellness brand",
        "display_name_en": "Skincare Flat Lay",
        "display_name_zh": "护肤品平铺",
        "description_en": "Clean Instagram-style flat lay for skincare products",
        "description_zh": "简洁Instagram风格的护肤品平铺摆拍",
        "category": "product",
        "tags": ["product", "skincare", "flatlay", "instagram"],
        "style_keywords": ["flat lay", "clean", "pastel"],
        "difficulty": "beginner",
        "use_count": 1020,
        "like_count": 285,
        "favorite_count": 160,
    },
    # ── architecture (5) ──
    {
        "prompt_text": "Futuristic skyscraper with organic flowing forms, parametric architecture, "
        "lush vertical gardens, glass and steel, blue sky background, "
        "Zaha Hadid inspired, sustainable design concept",
        "display_name_en": "Organic Skyscraper",
        "display_name_zh": "有机摩天楼",
        "description_en": "Parametric skyscraper design inspired by organic forms",
        "description_zh": "受有机形态启发的参数化摩天楼设计",
        "category": "architecture",
        "tags": ["architecture", "futuristic", "parametric", "sustainable"],
        "style_keywords": ["parametric", "Zaha Hadid", "organic"],
        "difficulty": "intermediate",
        "use_count": 580,
        "like_count": 198,
        "favorite_count": 110,
    },
    {
        "prompt_text": "Cozy Scandinavian interior design of a living room, "
        "hygge atmosphere, natural wood furniture, soft textiles, "
        "large windows with forest view, warm neutral palette, "
        "minimalist Nordic aesthetic",
        "display_name_en": "Nordic Living Room",
        "display_name_zh": "北欧客厅",
        "description_en": "Warm Scandinavian interior with hygge atmosphere",
        "description_zh": "温馨的北欧风格客厅设计",
        "category": "architecture",
        "tags": ["architecture", "interior", "scandinavian", "minimalist"],
        "style_keywords": ["Scandinavian", "hygge", "minimalist"],
        "difficulty": "beginner",
        "use_count": 940,
        "like_count": 320,
        "favorite_count": 185,
    },
    {
        "prompt_text": "Ancient Greek temple ruins at sunset, dramatic golden light, "
        "Corinthian columns, Mediterranean landscape, "
        "archaeological site photography, historical atmosphere, "
        "epic wide angle composition",
        "display_name_en": "Greek Temple Ruins",
        "display_name_zh": "希腊神殿遗迹",
        "description_en": "Dramatic sunset view of ancient Greek temple ruins",
        "description_zh": "古希腊神殿遗迹的戏剧性日落景色",
        "category": "architecture",
        "tags": ["architecture", "ruins", "greek", "historical"],
        "style_keywords": ["archaeological", "dramatic", "golden light"],
        "difficulty": "beginner",
        "use_count": 620,
        "like_count": 195,
        "favorite_count": 98,
    },
    {
        "prompt_text": "Japanese wabi-sabi tea house interior, tatami floors, shoji screens, "
        "single ikebana arrangement, morning light filtering through paper walls, "
        "meditative space, traditional craftsmanship, serene emptiness",
        "display_name_en": "Wabi-Sabi Tea House",
        "display_name_zh": "侘寂茶室",
        "description_en": "Serene Japanese tea house embodying wabi-sabi aesthetics",
        "description_zh": "体现侘寂美学的宁静日式茶室",
        "category": "architecture",
        "tags": ["architecture", "japanese", "interior", "traditional"],
        "style_keywords": ["wabi-sabi", "traditional", "serene"],
        "difficulty": "intermediate",
        "use_count": 480,
        "like_count": 175,
        "favorite_count": 105,
    },
    {
        "prompt_text": "Brutalist concrete building facade with geometric patterns, "
        "dramatic shadows and light, overcast sky, urban photography, "
        "raw concrete texture, bold geometric shapes, Le Corbusier inspired",
        "display_name_en": "Brutalist Facade",
        "display_name_zh": "粗野主义立面",
        "description_en": "Bold brutalist architecture with dramatic light and shadow",
        "description_zh": "戏剧性光影的大胆粗野主义建筑",
        "category": "architecture",
        "tags": ["architecture", "brutalist", "concrete", "urban"],
        "style_keywords": ["brutalist", "geometric", "raw concrete"],
        "difficulty": "beginner",
        "use_count": 390,
        "like_count": 142,
        "favorite_count": 72,
    },
    # ── anime (5) ──
    {
        "prompt_text": "Anime girl sitting on a rooftop at sunset watching the city skyline, "
        "Makoto Shinkai inspired lighting, vibrant sky gradient, "
        "detailed urban background, wind in hair, contemplative mood, "
        "your name movie aesthetic",
        "display_name_en": "Sunset Rooftop Anime",
        "display_name_zh": "日落天台动漫",
        "description_en": "Shinkai-style anime scene with breathtaking sunset sky",
        "description_zh": "新海诚风格的绝美日落天空动漫场景",
        "category": "anime",
        "tags": ["anime", "sunset", "shinkai", "city"],
        "style_keywords": ["Makoto Shinkai", "vibrant", "detailed"],
        "difficulty": "beginner",
        "use_count": 1450,
        "like_count": 480,
        "favorite_count": 280,
    },
    {
        "prompt_text": "Dark fantasy anime warrior in ornate black armor, "
        "wielding a glowing runic sword, stormy battlefield, "
        "dynamic action pose, flowing cape, epic lighting, "
        "detailed anime illustration, Berserk manga inspired",
        "display_name_en": "Dark Fantasy Warrior",
        "display_name_zh": "暗黑幻想战士",
        "description_en": "Epic dark fantasy anime warrior in dynamic battle pose",
        "description_zh": "动态战斗姿态的史诗暗黑幻想动漫战士",
        "category": "anime",
        "tags": ["anime", "fantasy", "warrior", "dark"],
        "style_keywords": ["dark fantasy", "dynamic", "epic"],
        "difficulty": "intermediate",
        "use_count": 890,
        "like_count": 345,
        "favorite_count": 195,
    },
    {
        "prompt_text": "Cute chibi anime characters in a cozy cafe, "
        "pastel color scheme, kawaii expressions, bubble tea, "
        "slice of life scene, warm lighting, detailed food items, "
        "soft rounded art style",
        "display_name_en": "Chibi Cafe Scene",
        "display_name_zh": "Q版咖啡馆",
        "description_en": "Adorable chibi characters enjoying a cozy cafe moment",
        "description_zh": "可爱Q版角色享受温馨咖啡馆时光",
        "category": "anime",
        "tags": ["anime", "chibi", "kawaii", "cafe"],
        "style_keywords": ["chibi", "kawaii", "pastel"],
        "difficulty": "beginner",
        "use_count": 1200,
        "like_count": 410,
        "favorite_count": 240,
    },
    {
        "prompt_text": "Cyberpunk anime character with neon-lit cybernetic augmentations, "
        "rain-soaked Neo Tokyo street, holographic advertisements, "
        "Ghost in the Shell inspired, detailed mechanical design, "
        "blue and pink neon palette",
        "display_name_en": "Cyberpunk Anime Neo",
        "display_name_zh": "赛博朋克动漫",
        "description_en": "Ghost in the Shell inspired cyberpunk anime character",
        "description_zh": "攻壳机动队风格的赛博朋克动漫角色",
        "category": "anime",
        "tags": ["anime", "cyberpunk", "sci-fi", "neon"],
        "style_keywords": ["cyberpunk", "Ghost in the Shell", "neon"],
        "difficulty": "intermediate",
        "use_count": 760,
        "like_count": 289,
        "favorite_count": 155,
    },
    {
        "prompt_text": "Studio Ghibli style anime landscape of a countryside village, "
        "lush green hills, fluffy cumulus clouds, magical atmosphere, "
        "Miyazaki inspired world-building, nostalgic summer day, "
        "detailed hand-painted background art",
        "display_name_en": "Ghibli Countryside",
        "display_name_zh": "吉卜力乡村",
        "description_en": "Miyazaki-inspired magical countryside in Ghibli art style",
        "description_zh": "宫崎骏风格的魔法乡村吉卜力画风",
        "category": "anime",
        "tags": ["anime", "ghibli", "landscape", "nostalgic"],
        "style_keywords": ["Studio Ghibli", "Miyazaki", "hand-painted"],
        "difficulty": "advanced",
        "use_count": 560,
        "like_count": 245,
        "favorite_count": 165,
    },
    # ── fantasy (5) ──
    {
        "prompt_text": "Enchanted forest with bioluminescent mushrooms and fireflies, "
        "magical fairy glen, soft ethereal glow, ancient twisted trees, "
        "fantasy concept art, mystical atmosphere, "
        "ray of moonlight piercing the canopy",
        "display_name_en": "Enchanted Mushroom Glen",
        "display_name_zh": "魔法蘑菇林地",
        "description_en": "Magical bioluminescent forest with an ethereal fairy-tale atmosphere",
        "description_zh": "超凡脱俗的童话氛围生物发光森林",
        "category": "fantasy",
        "tags": ["fantasy", "forest", "magical", "bioluminescent"],
        "style_keywords": ["ethereal", "bioluminescent", "mystical"],
        "difficulty": "beginner",
        "use_count": 1350,
        "like_count": 450,
        "favorite_count": 260,
    },
    {
        "prompt_text": "Epic dragon perched on a mountain peak above the clouds, "
        "scales glistening in sunlight, massive wingspan, "
        "fantasy illustration, detailed creature design, "
        "sense of awe and power, D&D inspired",
        "display_name_en": "Mountain Dragon",
        "display_name_zh": "山巅巨龙",
        "description_en": "Majestic dragon overlooking a cloud-covered mountain range",
        "description_zh": "俯瞰云海山脉的雄伟巨龙",
        "category": "fantasy",
        "tags": ["fantasy", "dragon", "epic", "creature"],
        "style_keywords": ["epic", "detailed", "D&D"],
        "difficulty": "intermediate",
        "use_count": 980,
        "like_count": 380,
        "favorite_count": 210,
    },
    {
        "prompt_text": "Floating island city with waterfalls cascading into clouds below, "
        "steampunk airships docked at sky ports, lush vegetation, "
        "fantasy world-building, detailed architecture, "
        "magical golden hour lighting",
        "display_name_en": "Sky Island City",
        "display_name_zh": "天空之城",
        "description_en": "Fantastical floating city with steampunk elements",
        "description_zh": "带有蒸汽朋克元素的奇幻浮空城市",
        "category": "fantasy",
        "tags": ["fantasy", "steampunk", "city", "floating"],
        "style_keywords": ["steampunk", "world-building", "detailed"],
        "difficulty": "advanced",
        "use_count": 480,
        "like_count": 210,
        "favorite_count": 130,
    },
    {
        "prompt_text": "Ancient wizard in a tower library filled with floating books and magical orbs, "
        "warm candlelight, swirling magical particles, "
        "fantasy character concept art, rich deep colors, "
        "intricate robes and staff design",
        "display_name_en": "Wizard's Library",
        "display_name_zh": "巫师图书馆",
        "description_en": "Atmospheric wizard study filled with magical artifacts",
        "description_zh": "充满魔法文物的氛围感巫师书房",
        "category": "fantasy",
        "tags": ["fantasy", "wizard", "library", "magical"],
        "style_keywords": ["concept art", "candlelight", "intricate"],
        "difficulty": "intermediate",
        "use_count": 720,
        "like_count": 265,
        "favorite_count": 150,
    },
    {
        "prompt_text": "Underwater fantasy kingdom with coral palace and merfolk, "
        "bioluminescent deep sea creatures, light rays from surface, "
        "vast ocean vista, jewel-toned color palette, "
        "detailed aquatic architecture, mythical atmosphere",
        "display_name_en": "Undersea Kingdom",
        "display_name_zh": "海底王国",
        "description_en": "Magnificent underwater fantasy realm with coral architecture",
        "description_zh": "珊瑚建筑的壮丽海底幻想王国",
        "category": "fantasy",
        "tags": ["fantasy", "underwater", "kingdom", "mythical"],
        "style_keywords": ["bioluminescent", "aquatic", "mythical"],
        "difficulty": "beginner",
        "use_count": 650,
        "like_count": 220,
        "favorite_count": 125,
    },
    # ── graphic-design (5) ──
    {
        "prompt_text": "Retro synthwave poster design with chrome text and neon grid, "
        "sunset gradient background, palm tree silhouettes, "
        "80s aesthetic, VHS artifacts, outrun style, "
        "vibrant purple pink and cyan palette",
        "display_name_en": "Synthwave Retro Poster",
        "display_name_zh": "合成波复古海报",
        "description_en": "Classic 80s synthwave poster with neon grid and sunset",
        "description_zh": "经典80年代合成波霓虹网格日落海报",
        "category": "graphic-design",
        "tags": ["graphic-design", "synthwave", "retro", "80s"],
        "style_keywords": ["synthwave", "outrun", "neon"],
        "difficulty": "beginner",
        "use_count": 1100,
        "like_count": 340,
        "favorite_count": 195,
    },
    {
        "prompt_text": "Swiss style typographic poster with bold Helvetica, geometric shapes, "
        "strict grid layout, limited color palette (red, black, white), "
        "International Typographic Style, clean modernist design, "
        "Josef Muller-Brockmann inspired",
        "display_name_en": "Swiss Typography Poster",
        "display_name_zh": "瑞士字体海报",
        "description_en": "Clean modernist poster in International Typographic Style",
        "description_zh": "国际主义字体设计风格的简洁现代主义海报",
        "category": "graphic-design",
        "tags": ["graphic-design", "typography", "swiss", "modernist"],
        "style_keywords": ["Swiss Style", "Helvetica", "grid"],
        "difficulty": "intermediate",
        "use_count": 430,
        "like_count": 165,
        "favorite_count": 88,
    },
    {
        "prompt_text": "Abstract geometric brand identity mockup with business cards and letterhead, "
        "modern minimalist design, golden ratio proportions, "
        "clean white space, professional presentation, "
        "brand guidelines layout",
        "display_name_en": "Brand Identity Mockup",
        "display_name_zh": "品牌形象样机",
        "description_en": "Professional brand identity presentation mockup",
        "description_zh": "专业品牌形象展示样机",
        "category": "graphic-design",
        "tags": ["graphic-design", "branding", "mockup", "minimal"],
        "style_keywords": ["minimalist", "professional", "brand identity"],
        "difficulty": "beginner",
        "use_count": 870,
        "like_count": 245,
        "favorite_count": 155,
    },
    {
        "prompt_text": "Psychedelic music festival poster with flowing liquid shapes, "
        "vibrant rainbow gradients, distorted typography, "
        "1960s counterculture aesthetic, trippy patterns, "
        "concert poster art, hand-lettering feel",
        "display_name_en": "Psychedelic Festival Poster",
        "display_name_zh": "迷幻音乐节海报",
        "description_en": "Vibrant psychedelic poster with 60s counterculture vibes",
        "description_zh": "60年代反文化风格的迷幻音乐节海报",
        "category": "graphic-design",
        "tags": ["graphic-design", "psychedelic", "music", "retro"],
        "style_keywords": ["psychedelic", "60s", "trippy"],
        "difficulty": "intermediate",
        "use_count": 520,
        "like_count": 198,
        "favorite_count": 95,
    },
    {
        "prompt_text": "Data visualization infographic with clean charts and icons, "
        "modern flat design, cohesive color system, "
        "information hierarchy, dashboard style layout, "
        "professional business presentation, clear typography",
        "display_name_en": "Data Dashboard Infographic",
        "display_name_zh": "数据仪表板信息图",
        "description_en": "Clean modern data visualization infographic design",
        "description_zh": "简洁现代的数据可视化信息图设计",
        "category": "graphic-design",
        "tags": ["graphic-design", "infographic", "data", "dashboard"],
        "style_keywords": ["flat design", "infographic", "clean"],
        "difficulty": "beginner",
        "use_count": 680,
        "like_count": 190,
        "favorite_count": 120,
    },
    # ── food (5) ──
    {
        "prompt_text": "Overhead shot of rustic Italian pasta dish, homemade tagliatelle, "
        "rich bolognese sauce, freshly grated parmesan, basil garnish, "
        "wooden table, warm natural lighting, food photography, "
        "cookbook quality composition",
        "display_name_en": "Italian Pasta Overhead",
        "display_name_zh": "意式面食俯拍",
        "description_en": "Mouthwatering overhead pasta shot with rustic Italian styling",
        "description_zh": "令人垂涎的意式面食俯拍照",
        "category": "food",
        "tags": ["food", "italian", "pasta", "overhead"],
        "style_keywords": ["rustic", "overhead", "natural lighting"],
        "difficulty": "beginner",
        "use_count": 1080,
        "like_count": 310,
        "favorite_count": 180,
    },
    {
        "prompt_text": "Japanese wagashi seasonal sweets arranged on ceramic plates, "
        "cherry blossom motifs, matcha powder dusting, "
        "traditional Japanese food styling, soft diffused light, "
        "minimalist composition, zen aesthetic",
        "display_name_en": "Japanese Wagashi Display",
        "display_name_zh": "和菓子展示",
        "description_en": "Elegant Japanese wagashi sweets with seasonal styling",
        "description_zh": "优雅的日式和菓子季节性摆盘",
        "category": "food",
        "tags": ["food", "japanese", "sweets", "traditional"],
        "style_keywords": ["Japanese", "minimalist", "zen"],
        "difficulty": "intermediate",
        "use_count": 520,
        "like_count": 195,
        "favorite_count": 110,
    },
    {
        "prompt_text": "Dramatic dark moody food photography of a chocolate lava cake, "
        "melting center flowing out, dark background, single spotlight, "
        "rich dark tones, steam rising, close-up detail, "
        "fine dining presentation",
        "display_name_en": "Dark Mood Chocolate Cake",
        "display_name_zh": "暗调巧克力蛋糕",
        "description_en": "Dramatic dark-mood chocolate lava cake with flowing center",
        "description_zh": "戏剧性暗调巧克力熔岩蛋糕",
        "category": "food",
        "tags": ["food", "chocolate", "dark-mood", "dessert"],
        "style_keywords": ["dark mood", "dramatic", "fine dining"],
        "difficulty": "intermediate",
        "use_count": 680,
        "like_count": 230,
        "favorite_count": 130,
    },
    {
        "prompt_text": "Colorful smoothie bowl with artistic toppings arrangement, "
        "acai base, dragon fruit, mango, granola, chia seeds, "
        "bright natural daylight, marble surface, "
        "Instagram food styling, top-down angle",
        "display_name_en": "Rainbow Smoothie Bowl",
        "display_name_zh": "彩虹果昔碗",
        "description_en": "Instagram-perfect colorful smoothie bowl with artistic toppings",
        "description_zh": "Instagram完美彩虹果昔碗艺术摆盘",
        "category": "food",
        "tags": ["food", "smoothie", "colorful", "healthy"],
        "style_keywords": ["Instagram", "colorful", "bright"],
        "difficulty": "beginner",
        "use_count": 950,
        "like_count": 280,
        "favorite_count": 165,
    },
    {
        "prompt_text": "Steaming hot ramen bowl with perfect soft-boiled egg, "
        "chashu pork, nori, scallions, rich tonkotsu broth, "
        "steam captured in motion, chopsticks lifting noodles, "
        "Japanese izakaya atmosphere, warm lighting",
        "display_name_en": "Tonkotsu Ramen Shot",
        "display_name_zh": "豚骨拉面摄影",
        "description_en": "Steaming ramen bowl with action shot of lifted noodles",
        "description_zh": "热气腾腾的拉面碗配夹面动态照",
        "category": "food",
        "tags": ["food", "ramen", "japanese", "action-shot"],
        "style_keywords": ["action shot", "steam", "warm"],
        "difficulty": "advanced",
        "use_count": 420,
        "like_count": 178,
        "favorite_count": 95,
    },
    # ── abstract (5) ──
    {
        "prompt_text": "Fluid abstract art with swirling marble textures, "
        "deep ocean blue and gold metallic paint, "
        "organic flowing forms, high resolution detail, "
        "luxury art print quality, fluid pour technique",
        "display_name_en": "Blue Gold Fluid Art",
        "display_name_zh": "蓝金流体艺术",
        "description_en": "Luxurious blue and gold fluid art with marble-like patterns",
        "description_zh": "大理石纹理的奢华蓝金流体艺术",
        "category": "abstract",
        "tags": ["abstract", "fluid", "marble", "luxury"],
        "style_keywords": ["fluid art", "marble", "metallic"],
        "difficulty": "beginner",
        "use_count": 920,
        "like_count": 278,
        "favorite_count": 155,
    },
    {
        "prompt_text": "Geometric abstract composition with overlapping translucent shapes, "
        "Bauhaus inspired color palette (primary colors + black), "
        "strong grid structure, Kandinsky inspired, "
        "clean edges, modern art museum quality",
        "display_name_en": "Bauhaus Geometry",
        "display_name_zh": "包豪斯几何",
        "description_en": "Bold Bauhaus-inspired geometric abstraction with primary colors",
        "description_zh": "受包豪斯启发的大胆几何抽象原色构成",
        "category": "abstract",
        "tags": ["abstract", "geometric", "bauhaus", "modernist"],
        "style_keywords": ["Bauhaus", "Kandinsky", "geometric"],
        "difficulty": "intermediate",
        "use_count": 450,
        "like_count": 165,
        "favorite_count": 88,
    },
    {
        "prompt_text": "Fractal patterns zoom into infinite complexity, "
        "Mandelbrot set inspired, cosmic colors (deep purple, electric blue, neon green), "
        "mathematical beauty, generative art, ultra detailed, "
        "psychedelic depth perception",
        "display_name_en": "Cosmic Fractal Zoom",
        "display_name_zh": "宇宙分形缩放",
        "description_en": "Mind-bending fractal art with cosmic color palette",
        "description_zh": "令人惊叹的宇宙色彩分形艺术",
        "category": "abstract",
        "tags": ["abstract", "fractal", "cosmic", "generative"],
        "style_keywords": ["fractal", "Mandelbrot", "generative"],
        "difficulty": "advanced",
        "use_count": 320,
        "like_count": 145,
        "favorite_count": 78,
    },
    {
        "prompt_text": "Minimalist abstract zen circle (enso) brush stroke on textured paper, "
        "Japanese ink wash style, imperfect beauty, single bold stroke, "
        "negative space, wabi-sabi philosophy, meditative art, "
        "sumi-e technique",
        "display_name_en": "Zen Enso Circle",
        "display_name_zh": "禅圆（圆相）",
        "description_en": "Minimalist zen enso circle in Japanese ink wash style",
        "description_zh": "日式水墨风格的极简禅圆",
        "category": "abstract",
        "tags": ["abstract", "zen", "japanese", "minimalist"],
        "style_keywords": ["sumi-e", "zen", "wabi-sabi"],
        "difficulty": "beginner",
        "use_count": 680,
        "like_count": 220,
        "favorite_count": 135,
    },
    {
        "prompt_text": "Vibrant color field painting inspired by Mark Rothko, "
        "large blocks of deep crimson and burnt orange, "
        "blurred edges between fields, emotional depth, "
        "gallery wall presentation, contemplative mood, "
        "abstract expressionism",
        "display_name_en": "Rothko Color Fields",
        "display_name_zh": "罗斯科色域",
        "description_en": "Contemplative color field painting in Rothko's style",
        "description_zh": "罗斯科风格的沉思色域绘画",
        "category": "abstract",
        "tags": ["abstract", "color-field", "expressionism", "rothko"],
        "style_keywords": ["Rothko", "color field", "abstract expressionism"],
        "difficulty": "beginner",
        "use_count": 510,
        "like_count": 185,
        "favorite_count": 105,
    },
]


async def seed() -> None:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        # Check if templates already exist
        from sqlalchemy import func, select

        count = await db.scalar(select(func.count()).select_from(PromptTemplate))
        if count and count > 0:
            print(f"Found {count} existing templates. Skipping seed.")
            return

        for i, tpl in enumerate(TEMPLATES):
            # Vary creation times: spread across last 30 days
            hours_ago = (len(TEMPLATES) - i) * 14.4  # ~30 days spread
            created_at = now - timedelta(hours=hours_ago)

            use_count: int = tpl.pop("use_count", 0)  # type: ignore[assignment]
            like_count: int = tpl.pop("like_count", 0)  # type: ignore[assignment]
            favorite_count: int = tpl.pop("favorite_count", 0)  # type: ignore[assignment]

            trending = compute_trending(like_count, use_count, favorite_count, hours_ago)

            template = PromptTemplate(
                id=str(uuid4()),
                **tpl,
                parameters={},
                language="bilingual",
                source="curated",
                use_count=use_count,
                like_count=like_count,
                favorite_count=favorite_count,
                trending_score=round(trending, 6),
                is_active=True,
                created_at=created_at,
            )
            db.add(template)

        await db.commit()
        print(f"Seeded {len(TEMPLATES)} prompt templates successfully.")


if __name__ == "__main__":
    asyncio.run(seed())
