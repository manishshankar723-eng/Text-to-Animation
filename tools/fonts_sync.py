"""Fetch, pin and MEASURE the bundled caption fonts. Run when the list changes.

    python tools/fonts_sync.py            # report only, touches nothing
    python tools/fonts_sync.py --write    # download, instance, write the files
    python tools/fonts_sync.py --write --only devanagari,arabic

⚠ THIS TOOL EXISTS BECAUSE THREE FACTS ABOUT A FONT CANNOT BE TYPED BY HAND and
one of them is invisible until a customer's video is wrong:

  `line_ratio`  (ascent + descent) ÷ size. The browser spaces lines by it. Guess
                it and a two-line caption sits differently in the monitor and in
                the MP4 — the exact bug `animatic_fonts.py` was written to kill.
  `scripts`     WHICH WRITING SYSTEMS THE FILE CAN ACTUALLY DRAW, read out of
                its cmap rather than assumed from its name. "Poppins supports
                Hindi" is a thing everyone believes; whether THIS cut of Poppins
                has every matra in it is a thing only the file knows. A font
                offered for a language it cannot draw renders ▯▯▯, and it does
                so in the export, after the customer has paid for the render.
  `copyright`   The OFL requires the notice to travel with the binary. It is in
                the file's own name table; copying it by hand is how a licence
                line ends up attached to the wrong font.

The fourth thing it does is INSTANCE. Most of Google Fonts is variable now — one
file with a weight axis. A variable font is not safe here: the browser would
pick an instance via CSS `font-weight` and Pillow would pick one via
`set_variation_by_name`, which is two mechanisms agreeing by luck. So a family
that only ships variable is frozen to a single static weight HERE, once, and
both sides then load one ordinary .ttf with one weight in it. Same rule the
folder README already states: one weight per family, and a second weight is a
second entry.

Nothing here runs at request time. It writes `client/public/fonts/` and prints
the entries to paste into the twin lists; `tests/captions_check.py` is what
holds the result honest afterwards.

Needs `fonttools` (see requirements-dev.txt) and network access to
raw.githubusercontent.com.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "client", "public", "fonts")
RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl/{slug}/{file}"
API = "https://api.github.com/repos/google/fonts/contents/ofl/{slug}"

# ---------------------------------------------------------------------------
# What a script means, in characters
# ---------------------------------------------------------------------------
# ⚠ A FONT "COVERS" A SCRIPT WHEN IT HAS EVERY ONE OF THESE, no exceptions and
# no percentage. The probe is the ordinary working set of the writing system —
# its letters, its vowel signs, its virama, its digits where they are in daily
# use — not its full Unicode block. Rare and historic characters are left out on
# purpose: requiring them would reject faces that render every real caption
# perfectly, and allowing a percentage would ship a face that renders ▯ for one
# letter a customer happens to need.
#
# ⚠ TWINNED, in effect, with `SCRIPTS` in `animatic_fonts.py` — the ids here are
# the ids that end up on the font list, and `tests/captions_check.py` fails if a
# font declares a script this table does not know.
SCRIPT_PROBES: dict[str, str] = {
    # ⚠ PLAIN ASCII IS ITS OWN ID, and it earns it. Display faces built for one
    # writing system routinely carry the unaccented alphabet and nothing else —
    # Black Han Sans has A-Z and no é — and a Korean title reading "BTS 2024"
    # is a completely ordinary thing to want. Without this id such a face would
    # be marked as having no Latin at all and the editor would warn about text
    # it draws perfectly.
    "latin-basic": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        " .,:;!?'\"()-&%#@/+*="
    ),
    # Western European + the punctuation a caption is actually typed with. A
    # font that passes this has passed `latin-basic` too — the probe contains it.
    "latin": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        " .,:;!?'\"()-–—…&%#@/+*="
        "ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÑÒÓÔÕÖÙÚÛÜÝßàáâãäåçèéêëìíîïñòóôõöùúûüýÿ"
    ),
    # Polish, Turkish, Czech, Slovak, Hungarian, Romanian, Croatian, Baltic.
    "latin-ext": "ĄąĆćĘęŁłŃńŚśŹźŻżĞğİıŞşČčĎďĚěŇňŘřŠšŤťŮůŽžŐőŰűĂăȘșȚțĐđĪīŪūĖėĮįŲų",
    # Vietnamese is Latin with a stack of its own — a font that has Polish need
    # not have this, so it is its own id.
    "vietnamese": "ĂăÂâĐđÊêÔôƠơƯưẠạẢảẤấẦầẨẩẪẫẬậẮắẰằẲẳẴẵẶặẸẹẺẻẼẽẾếỀềỂểỄễỆệỈỉỊịỌọỎỏỐốỒồỔổỖỗỘộỚớỜờỞởỠỡỢợỤụỦủỨứỪừỬửỮữỰựỲỳỴỵỶỷỸỹ",
    "cyrillic": (
        "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
        "ЄєІіЇїҐґЎў"  # Ukrainian and Belarusian, which plain Russian sets miss
    ),
    "greek": "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρστυφχψωςάέήίόύώΐΰϊϋ",
    # --- The scripts that also need SHAPING (see text_shaping.py) -----------
    "devanagari": (
        "अआइईउऊऋएऐओऔअंअः"
        "कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह"
        "क़ख़ग़ज़ड़ढ़फ़"
        "ािीुूृॄेैोौंःँ्ॅॉ"
        "।०१२३४५६७८९"
    ),
    "gurmukhi": (
        "ਅਆਇਈਉਊਏਐਓਔ"
        "ਕਖਗਘਙਚਛਜਝਞਟਠਡਢਣਤਥਦਧਨਪਫਬਭਮਯਰਲਲ਼ਵੜਸ਼ਸਹ"
        "ਖ਼ਗ਼ਜ਼ਫ਼"
        "ਾਿੀੁੂੇੈੋੌੰੱ੍ੑ਼"
        "੦੧੨੩੪੫੬੭੮੯"
    ),
    "bengali": (
        "অআইঈউঊঋএঐওঔ"
        "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়"
        "ািীুূৃেৈোৌং ঁঃ্"
        "০১২৩৪৫৬৭৮৯"
    ),
    "gujarati": (
        "અઆઇઈઉઊઋએઐઓઔ"
        "કખગઘઙચછજઝઞટઠડઢણતથદધનપફબભમયરલળવશષસહ"
        "ાિીુૂૃેૈોૌંઃઁ્"
        "૦૧૨૩૪૫૬૭૮૯"
    ),
    "odia": (
        "ଅଆଇଈଉଊଋଏଐଓଔ"
        "କଖଗଘଙଚଛଜଝଞଟଠଡଢଣତଥଦଧନପଫବଭମଯରଲଳଵଶଷସହ"
        "ାିୀୁୂୃେୈୋୌଂଃଁ୍"
        "୦୧୨୩୪୫୬୭୮୯"
    ),
    "tamil": (
        "அஆஇஈஉஊஎஏஐஒஓஔ"
        "கஙசஞடணதநனபமயரறலளழவஸஷஹஜ"
        "ாிீுூெேைொோௌ்ஃ"
        "௦௧௨௩௪௫௬௭௮௯"
    ),
    "telugu": (
        "అఆఇఈఉఊఋఎఏఐఒఓఔ"
        "కఖగఘఙచఛజఝఞటఠడఢణతథదధనపఫబభమయరఱలళవశషసహ"
        "ాిీుూృెేైొోౌంః్"
        "౦౧౨౩౪౫౬౭౮౯"
    ),
    "kannada": (
        "ಅಆಇಈಉಊಋಎಏಐಒಓಔ"
        "ಕಖಗಘಙಚಛಜಝಞಟಠಡಢಣತಥದಧನಪಫಬಭಮಯರಱಲಳವಶಷಸಹ"
        "ಾಿೀುೂೃೆೇೈೊೋೌಂಃ್"
        "೦೧೨೩೪೫೬೭೮೯"
    ),
    "malayalam": (
        "അആഇഈഉഊഋഎഏഐഒഓഔ"
        "കഖഗഘങചഛജഝഞടഠഡഢണതഥദധനപഫബഭമയരറലളഴവശഷസഹ"
        "ാിീുൂൃെേൈൊോൌംഃ്ൗ"
        "൦൧൨൩൪൫൬൭൮൯"
    ),
    "sinhala": (
        "අආඇඈඉඊඋඌඑඒඓඔඕ"
        "කඛගඝඞචඡජඣඤටඨඩඪණතථදධනපඵබභමයරලවශෂසහළෆ"
        "ාැෑිීුූෙේෛොෝෞ්ං"
    ),
    # Arabic AS ARABIC IS WRITTEN — the letters, the harakat, both sets of
    # digits, the comma and question mark that face the other way.
    "arabic": (
        "ابتثجحخدذرزسشصضطظعغفقكلمنهوىيء"
        "أإآؤئةٱ"
        "ًٌٍَُِّْ"
        "٠١٢٣٤٥٦٧٨٩،؛؟"
    ),
    # ⚠ URDU IS NOT ARABIC WITH A DIFFERENT LANGUAGE TAG. It needs letters
    # Arabic does not have — retroflex ٹ ڈ ڑ, the do-chashmi ھ that makes every
    # aspirated consonant, the ے that ends half its words — and a face missing
    # them renders ▯ in the middle of ordinary sentences. Cairo is a good Arabic
    # font and fails this on purpose. (Persian's پ چ ژ گ are in here too; a face
    # that has the Urdu set has always had those.)
    "urdu": (
        "ابتثجحخدذرزسشصضطظعغفقلمنوهیءآأؤئة"
        "پچژگکٹڈڑںھہۂۃےۓ"
        "۰۱۲۳۴۵۶۷۸۹،؛؟"
    ),
    "hebrew": "אבגדהוזחטיכךלמםנןסעפףצץקרשת ְֱֲֳִֵֶַָֹֻּׁׂ ״׳",
    "thai": (
        "กขคฆงจฉชซญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"
        "ะัาำิีึืุูเแโใไ"
        "่้๊๋์ํฯๆ"
        "๐๑๒๓๔๕๖๗๘๙"
    ),
    # --- CJK: no shaping needed, but an enormous cmap ------------------------
    # ⚠ A SAMPLE, NOT A SET. Han is tens of thousands of characters and no probe
    # string can stand for it; these are the most common few hundred, which a
    # face either has wholesale or is not a CJK face at all.
    #
    # ⚠ AND IT IS THREE PROBES, NOT ONE, because the three regions do not use
    # the same characters. Measured, not assumed: Noto Sans TC does not have 这
    # 说 们 军 当 进 将 还 没, Noto Sans SC has the traditional forms as well as
    # its own, and M PLUS Rounded has the Japanese set and neither Chinese one.
    # A single `han` id would have declared all of them equal and shipped a
    # Chinese caption with holes in it.
    "han-sc": "的一是不了人我在有他这为之大来以个中上们到说国和地也子时道出而要于就下得可你年生自会那后能对着事其里所去行过家十用发天如然作方成者多日都三小军二无同么经法当起与好看学进种将还分此心前面又定见只主没公从",
    "han-tc": "的一是不了人我在有他這為之大來以個中上們到說國和地也子時道出而要於就下得可你年生自會那後能對著事其裡所去行過家十用發天如然作方成者多日都三小軍二無同麼經法當起與好看學進種將還分此心前面又定見只主沒公從",
    "han-jp": "日本人年大中国出時行見月生後前間分方新学高長業実力体目手心内思会社上下小山川田口耳足雨天気火水木金土曜朝昼夜春夏秋冬東西南北語話読書聞食飲買売車駅道店家校先友父母兄弟姉妹電話今来毎週",
    "kana": (
        "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
        "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽっゃゅょー"
        "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
        "ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポッャュョ"
        "、。「」・"
    ),
    "hangul": (
        "가나다라마바사아자차카타파하"
        "각간갈감갑강개객거건걸검겁게겨격견결경계고곡곤골공과관광교구국군굴궁권귀규균그근글금급기긴길김"
        "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅑㅓㅕㅗㅛㅜㅠㅡㅣ"
    ),
}

# ---------------------------------------------------------------------------
# The families, and which cut of each one is bundled
# ---------------------------------------------------------------------------
# `style` is the static file to take if the family ships statics. `wght` is the
# weight to FREEZE the variable font at when it does not — the same number, so
# that "Bold" means the same thing either way. `group` is only used to filter a
# run with `--only`.
WANTED: tuple[dict, ...] = (
    # --- Devanagari: Hindi, Marathi, Nepali, Bhojpuri, Sanskrit -------------
    {"id": "noto-devanagari", "label": "Noto Sans Devanagari", "slug": "notosansdevanagari", "style": "SemiBold", "wght": 600, "group": "devanagari"},
    {"id": "mukta", "label": "Mukta", "slug": "mukta", "style": "Bold", "wght": 700, "group": "devanagari"},
    {"id": "rozha", "label": "Rozha One", "slug": "rozhaone", "style": "Regular", "wght": 400, "group": "devanagari"},
    {"id": "baloo2", "label": "Baloo 2", "slug": "baloo2", "style": "ExtraBold", "wght": 800, "group": "devanagari"},
    {"id": "tiro-devanagari", "label": "Tiro Devanagari Hindi", "slug": "tirodevanagarihindi", "style": "Regular", "wght": 400, "group": "devanagari"},
    # --- Gurmukhi: Punjabi ---------------------------------------------------
    {"id": "noto-gurmukhi", "label": "Noto Sans Gurmukhi", "slug": "notosansgurmukhi", "style": "SemiBold", "wght": 600, "group": "gurmukhi"},
    {"id": "mukta-mahee", "label": "Mukta Mahee", "slug": "muktamahee", "style": "Bold", "wght": 700, "group": "gurmukhi"},
    {"id": "baloo-paaji", "label": "Baloo Paaji 2", "slug": "baloopaaji2", "style": "ExtraBold", "wght": 800, "group": "gurmukhi"},
    # --- Bengali: Bangla, Assamese ------------------------------------------
    {"id": "noto-bengali", "label": "Noto Sans Bengali", "slug": "notosansbengali", "style": "SemiBold", "wght": 600, "group": "bengali"},
    {"id": "hind-siliguri", "label": "Hind Siliguri", "slug": "hindsiliguri", "style": "Bold", "wght": 700, "group": "bengali"},
    {"id": "baloo-da", "label": "Baloo Da 2", "slug": "balooda2", "style": "ExtraBold", "wght": 800, "group": "bengali"},
    # --- Gujarati ------------------------------------------------------------
    {"id": "noto-gujarati", "label": "Noto Sans Gujarati", "slug": "notosansgujarati", "style": "SemiBold", "wght": 600, "group": "gujarati"},
    {"id": "baloo-bhai", "label": "Baloo Bhai 2", "slug": "baloobhai2", "style": "ExtraBold", "wght": 800, "group": "gujarati"},
    # --- Odia ----------------------------------------------------------------
    {"id": "noto-odia", "label": "Noto Sans Oriya", "slug": "notosansoriya", "style": "SemiBold", "wght": 600, "group": "odia"},
    # --- Tamil ---------------------------------------------------------------
    {"id": "noto-tamil", "label": "Noto Sans Tamil", "slug": "notosanstamil", "style": "SemiBold", "wght": 600, "group": "tamil"},
    {"id": "mukta-malar", "label": "Mukta Malar", "slug": "muktamalar", "style": "Bold", "wght": 700, "group": "tamil"},
    {"id": "baloo-thambi", "label": "Baloo Thambi 2", "slug": "baloothambi2", "style": "ExtraBold", "wght": 800, "group": "tamil"},
    # --- Telugu --------------------------------------------------------------
    {"id": "noto-telugu", "label": "Noto Sans Telugu", "slug": "notosanstelugu", "style": "SemiBold", "wght": 600, "group": "telugu"},
    {"id": "baloo-tammudu", "label": "Baloo Tammudu 2", "slug": "balootammudu2", "style": "ExtraBold", "wght": 800, "group": "telugu"},
    # --- Kannada -------------------------------------------------------------
    {"id": "noto-kannada", "label": "Noto Sans Kannada", "slug": "notosanskannada", "style": "SemiBold", "wght": 600, "group": "kannada"},
    {"id": "baloo-tamma", "label": "Baloo Tamma 2", "slug": "balootamma2", "style": "ExtraBold", "wght": 800, "group": "kannada"},
    # --- Malayalam -----------------------------------------------------------
    {"id": "noto-malayalam", "label": "Noto Sans Malayalam", "slug": "notosansmalayalam", "style": "SemiBold", "wght": 600, "group": "malayalam"},
    {"id": "baloo-chettan", "label": "Baloo Chettan 2", "slug": "baloochettan2", "style": "ExtraBold", "wght": 800, "group": "malayalam"},
    # --- Arabic, and Urdu which needs its own hand ---------------------------
    {"id": "noto-arabic", "label": "Noto Sans Arabic", "slug": "notosansarabic", "style": "SemiBold", "wght": 600, "group": "arabic"},
    {"id": "cairo", "label": "Cairo", "slug": "cairo", "style": "Bold", "wght": 700, "group": "arabic"},
    {"id": "amiri", "label": "Amiri", "slug": "amiri", "style": "Bold", "wght": 700, "group": "arabic"},
    {"id": "noto-nastaliq", "label": "Noto Nastaliq Urdu", "slug": "notonastaliqurdu", "style": "Bold", "wght": 700, "group": "arabic"},
    # --- Hebrew --------------------------------------------------------------
    {"id": "noto-hebrew", "label": "Noto Sans Hebrew", "slug": "notosanshebrew", "style": "SemiBold", "wght": 600, "group": "hebrew"},
    {"id": "heebo", "label": "Heebo", "slug": "heebo", "style": "Bold", "wght": 700, "group": "hebrew"},
    # --- Thai ----------------------------------------------------------------
    {"id": "noto-thai", "label": "Noto Sans Thai", "slug": "notosansthai", "style": "SemiBold", "wght": 600, "group": "thai"},
    {"id": "kanit", "label": "Kanit", "slug": "kanit", "style": "Bold", "wght": 700, "group": "thai"},
    # --- Chinese, Japanese, Korean -------------------------------------------
    {"id": "noto-sc", "label": "Noto Sans SC", "slug": "notosanssc", "style": "Bold", "wght": 700, "group": "cjk"},
    {"id": "noto-serif-sc", "label": "Noto Serif SC", "slug": "notoserifsc", "style": "Bold", "wght": 700, "group": "cjk"},
    {"id": "noto-tc", "label": "Noto Sans TC", "slug": "notosanstc", "style": "Bold", "wght": 700, "group": "cjk"},
    {"id": "noto-jp", "label": "Noto Sans JP", "slug": "notosansjp", "style": "Bold", "wght": 700, "group": "cjk"},
    {"id": "mplus-rounded", "label": "M PLUS Rounded 1c", "slug": "mplusrounded1c", "style": "Bold", "wght": 700, "group": "cjk"},
    {"id": "noto-kr", "label": "Noto Sans KR", "slug": "notosanskr", "style": "Bold", "wght": 700, "group": "cjk"},
    {"id": "black-han", "label": "Black Han Sans", "slug": "blackhansans", "style": "Regular", "wght": 400, "group": "cjk"},
    # --- Latin, Cyrillic, Greek: broaden what the existing fourteen cover ----
    {"id": "noto-sans", "label": "Noto Sans", "slug": "notosans", "style": "SemiBold", "wght": 600, "group": "european"},
    {"id": "noto-serif", "label": "Noto Serif", "slug": "notoserif", "style": "SemiBold", "wght": 600, "group": "european"},
    {"id": "rubik", "label": "Rubik", "slug": "rubik", "style": "Bold", "wght": 700, "group": "european"},
    {"id": "be-vietnam", "label": "Be Vietnam Pro", "slug": "bevietnampro", "style": "Bold", "wght": 700, "group": "european"},
)

# The fonts that were here before any of this — MEASURED ONLY, under `--local`.
# ⚠ NEVER RE-DOWNLOADED. Every animatic exported so far was drawn with these
# exact files; a fresh cut from upstream would have different metrics and would
# re-wrap captions in projects nobody has opened. The `style` is the one in the
# filename and the `slug` is only recorded so the provenance is not lost.
ORIGINAL_FOURTEEN: tuple[dict, ...] = (
    {"id": "inter", "label": "Inter", "slug": "inter", "style": "SemiBold", "wght": 600, "group": "original"},
    {"id": "montserrat", "label": "Montserrat", "slug": "montserrat", "style": "SemiBold", "wght": 600, "group": "original"},
    {"id": "poppins", "label": "Poppins", "slug": "poppins", "style": "SemiBold", "wght": 600, "group": "original"},
    {"id": "nunito", "label": "Nunito", "slug": "nunito", "style": "Bold", "wght": 700, "group": "original"},
    {"id": "anton", "label": "Anton", "slug": "anton", "style": "Regular", "wght": 400, "group": "original"},
    {"id": "bebas", "label": "Bebas Neue", "slug": "bebasneue", "style": "Regular", "wght": 400, "group": "original"},
    {"id": "oswald", "label": "Oswald", "slug": "oswald", "style": "Medium", "wght": 500, "group": "original"},
    {"id": "archivo", "label": "Archivo Black", "slug": "archivoblack", "style": "Regular", "wght": 400, "group": "original"},
    {"id": "playfair", "label": "Playfair Display", "slug": "playfairdisplay", "style": "SemiBold", "wght": 600, "group": "original"},
    {"id": "merriweather", "label": "Merriweather", "slug": "merriweather", "style": "Bold", "wght": 700, "group": "original"},
    {"id": "bangers", "label": "Bangers", "slug": "bangers", "style": "Regular", "wght": 400, "group": "original"},
    {"id": "lobster", "label": "Lobster", "slug": "lobster", "style": "Regular", "wght": 400, "group": "original"},
    {"id": "caveat", "label": "Caveat", "slug": "caveat", "style": "SemiBold", "wght": 600, "group": "original"},
    {"id": "courier", "label": "Courier Prime", "slug": "courierprime", "style": "Regular", "wght": 400, "group": "original"},
)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fonts-sync"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _listing(slug: str) -> list[str]:
    """Every filename in `ofl/<slug>/`, statics and variable alike."""
    data = json.loads(_get(API.format(slug=slug)).decode("utf-8"))
    names = [entry["name"] for entry in data if entry.get("type") == "file"]
    for entry in data:
        # Several families keep their statics in a `static/` subfolder.
        if entry.get("type") == "dir" and entry["name"] == "static":
            sub = json.loads(_get(entry["url"]).decode("utf-8"))
            names += ["static/" + e["name"] for e in sub if e.get("type") == "file"]
    return names


def fetch_ttf(want: dict) -> tuple[bytes, str, bool]:
    """The .ttf bytes for `want`, its upstream filename, and whether it was frozen.

    Prefers a static file in the requested style. Falls back to the variable
    font frozen at `wght` — see the module docstring for why a variable font
    never ships from here as-is.
    """
    names = _listing(want["slug"])
    ttfs = [n for n in names if n.lower().endswith(".ttf")]
    style = want["style"]
    for name in ttfs:
        base = os.path.basename(name)
        if base.endswith(f"-{style}.ttf") and "Italic" not in base:
            return _get(RAW.format(slug=want["slug"], file=name)), name, False
    variable = [n for n in ttfs if "[" in os.path.basename(n)]
    if not variable:
        raise SystemExit(f"  {want['id']}: no -{style}.ttf and no variable font in {ttfs}")
    name = variable[0]
    raw = _get(RAW.format(slug=want["slug"], file=urllib.parse.quote(name)))
    return _freeze(raw, want["wght"]), name, True


def _freeze(raw: bytes, wght: int) -> bytes:
    """A variable font cut down to one static weight.

    Every other axis is left at its default: `wdth`, `opsz` and the rest are not
    things a caption picker offers, so pinning them keeps the file to one
    unambiguous design rather than one the two renderers each have to agree
    about.
    """
    from fontTools import ttLib
    from fontTools.varLib import instancer

    font = ttLib.TTFont(io.BytesIO(raw))
    axes = {a.axisTag: a for a in font["fvar"].axes}
    location = {tag: axis.defaultValue for tag, axis in axes.items()}
    if "wght" in axes:
        axis = axes["wght"]
        location["wght"] = max(axis.minValue, min(axis.maxValue, wght))
    instancer.instantiateVariableFont(font, location, inplace=True, updateFontNames=True)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------
def cmap_of(raw: bytes) -> set[int]:
    """Every codepoint the file can actually DRAW.

    ⚠ NOT JUST "is it in the cmap". A cmap entry only says a codepoint maps to a
    glyph; it does not say the glyph has any ink in it. Fonts routinely map a
    character to an empty outline — a subsetted build, a placeholder left in, a
    mark the designer never drew — and that renders as a blank gap, which on a
    title card is indistinguishable from a font that "works" until somebody
    reads the finished video.

    ⚠ AND AN EMPTY GLYPH IS NOT ALWAYS A MISSING ONE. Three exemptions, each of
    which cost a wrong answer before it was added:

      combining marks and spaces  legitimately have no outline of their own.

      anything GSUB rewrites      In Noto Nastaliq Urdu EVERY Arabic letter is
                                  an empty glyph: `ب` is a stub that the shaper
                                  substitutes for one of its contextual forms
                                  before anything is drawn. Judged on outlines
                                  alone, the best Urdu font in the world reads
                                  as having no Urdu in it — which is exactly
                                  what this function first concluded.

      CFF outlines                `glyf` is a TrueType table; a font with CFF
                                  outlines has no cheap emptiness test, so it
                                  is taken at its cmap's word.
    """
    import unicodedata

    from fontTools import ttLib

    font = ttLib.TTFont(io.BytesIO(raw), fontNumber=0)
    names: dict[int, str] = {}
    for table in font["cmap"].tables:
        if table.isUnicode():
            names.update(table.cmap)
    covered = set(names)
    glyf = font["glyf"] if "glyf" in font else None
    if glyf is None:
        return covered

    shaped = _gsub_inputs(font)
    empty = set()
    for code, glyph in names.items():
        if unicodedata.category(chr(code)) in ("Mn", "Mc", "Me", "Zs", "Cf"):
            continue
        if glyph in shaped:
            continue
        try:
            if glyf[glyph].numberOfContours == 0:
                empty.add(code)
        except KeyError:
            empty.add(code)
    return covered - empty


def _gsub_inputs(font) -> set[str]:
    """Every glyph name the shaper is allowed to substitute or reposition.

    A glyph in here may be an empty stub and the text still draws — see
    `cmap_of`. Both GSUB and GPOS are read: a mark that only ever appears
    positioned by GPOS is in the same position as a letter only ever reached
    through GSUB.

    ⚠ IT RECURSES, and has to. Noto Nastaliq Urdu wraps its lookups in EXTENSION
    subtables (lookup type 7), so a walk one level deep finds no coverage at all
    and concludes the font contains no Arabic — which is what the first version
    of this did.
    """
    names: set[str] = set()

    def walk(node, depth: int = 0) -> None:
        if node is None or depth > 8:
            return
        coverage = getattr(node, "Coverage", None)
        for cov in coverage if isinstance(coverage, list) else [coverage]:
            if cov is not None and getattr(cov, "glyphs", None):
                names.update(cov.glyphs)
        for attr in ("mapping", "alternates", "ligatures"):
            value = getattr(node, attr, None)
            if isinstance(value, dict):
                names.update(value.keys())
        for attr in ("ExtSubTable", "SubTable", "BacktrackCoverage", "InputCoverage", "LookAheadCoverage"):
            value = getattr(node, attr, None)
            if isinstance(value, list):
                for item in value:
                    if getattr(item, "glyphs", None):
                        names.update(item.glyphs)
                    else:
                        walk(item, depth + 1)
            elif value is not None:
                walk(value, depth + 1)

    for tag in ("GSUB", "GPOS"):
        if tag not in font:
            continue
        lookup_list = getattr(font[tag].table, "LookupList", None)
        for lookup in getattr(lookup_list, "Lookup", None) or []:
            walk(lookup)
    return names


def scripts_of(raw: bytes) -> tuple[list[str], dict[str, str]]:
    """Which script ids this file covers, and what the near-misses were missing.

    The misses are printed rather than swallowed: "Poppins is one nukta short of
    Devanagari" is the sentence worth seeing before deciding what to offer.
    """
    covered = cmap_of(raw)
    have: list[str] = []
    near: dict[str, str] = {}
    for script, probe in SCRIPT_PROBES.items():
        missing = sorted({ch for ch in probe if ch.strip() and ord(ch) not in covered})
        if not missing:
            have.append(script)
        elif len(missing) <= max(4, len(set(probe)) // 20):
            near[script] = "".join(missing)
    return have, near


def line_ratio_of(path: str) -> float:
    """(ascent + descent) ÷ size, the number the browser spaces lines by."""
    from PIL import ImageFont

    return round(sum(ImageFont.truetype(path, 100).getmetrics()) / 100, 2)


def copyright_of(raw: bytes) -> str:
    from fontTools import ttLib

    font = ttLib.TTFont(io.BytesIO(raw), fontNumber=0, lazy=True)
    for record in font["name"].names:
        if record.nameID == 0:
            try:
                return " ".join(str(record).split())
            except Exception:
                continue
    return ""


def css_family(font_id: str) -> str:
    """`AnimaticNotoDevanagari` from `noto-devanagari`.

    ⚠ NEVER the font's real family name. A user with Noto Sans installed would
    otherwise be served their own copy by the browser and ours by the exporter,
    which is the single divergence this whole arrangement exists to prevent.
    """
    return "Animatic" + "".join(part.capitalize() for part in font_id.split("-"))


def file_name(label: str, style: str) -> str:
    return "".join(label.split()) + f"-{style}.ttf"


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="actually write the .ttf files")
    ap.add_argument("--only", default="", help="comma-separated groups to run")
    ap.add_argument("--out", default="", help="write the measured manifest here as JSON")
    ap.add_argument(
        "--local",
        action="store_true",
        help="re-measure the files already in client/public/fonts/ instead of "
        "downloading. The measurements are the part that changes when a probe "
        "is corrected; the 56 MB of .ttf is not.",
    )
    args = ap.parse_args()

    groups = {g.strip() for g in args.only.split(",") if g.strip()}
    wanted = [w for w in WANTED if not groups or w["group"] in groups]
    if args.local:
        # ⚠ THE ORIGINAL FOURTEEN ARE MEASURED, NEVER RE-FETCHED. Their .ttf
        # files are what every animatic made so far was rendered with; pulling a
        # fresh cut from upstream would change their metrics and re-wrap
        # captions in projects nobody has touched. What they DO need is the same
        # honest `scripts` reading as the new ones — "Poppins does Hindi" was
        # believed rather than measured, and this is where that gets checked.
        wanted = [w for w in ORIGINAL_FOURTEEN if not groups or w["group"] in groups] + wanted
    os.makedirs(FONT_DIR, exist_ok=True)

    manifest: list[dict] = []
    total = 0
    for want in wanted:
        name = file_name(want["label"], want["style"])
        path = os.path.join(FONT_DIR, name)
        if args.local:
            if not os.path.isfile(path):
                print(f"  FAIL {want['id']}: {name} is not in {FONT_DIR}")
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            upstream, frozen = f"ofl/{want['slug']}/ (local)", False
        else:
            try:
                raw, upstream, frozen = fetch_ttf(want)
            except Exception as exc:  # noqa: BLE001 — one family must not stop the run
                print(f"  FAIL {want['id']}: {exc}")
                continue
        if args.write and not args.local:
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(raw)
            os.replace(tmp, path)
        elif not os.path.isfile(path):
            tmp = os.path.join(FONT_DIR, ".probe.ttf")
            with open(tmp, "wb") as fh:
                fh.write(raw)
            path = tmp

        scripts, near = scripts_of(raw)
        entry = {
            "id": want["id"],
            "label": want["label"],
            "file": name,
            "family": css_family(want["id"]),
            "line_ratio": line_ratio_of(path),
            "scripts": scripts,
            "copyright": copyright_of(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "upstream": f"ofl/{want['slug']}/{upstream}" + (f" frozen at wght {want['wght']}" if frozen else ""),
        }
        manifest.append(entry)
        total += len(raw)
        flag = "  (frozen)" if frozen else ""
        print(f"  {want['id']:<18} {len(raw)/1024:7.0f} KB  ratio {entry['line_ratio']:.2f}  {','.join(scripts) or 'NOTHING'}{flag}")
        if near:
            for script, missing in near.items():
                print(f"      near-miss {script}: missing {missing}")
        if path.endswith(".probe.ttf"):
            os.remove(path)

    print(f"\n  {len(manifest)} fonts, {total/1024/1024:.1f} MB total")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)
        print(f"  manifest → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
