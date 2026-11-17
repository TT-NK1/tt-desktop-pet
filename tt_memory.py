"""TT's local, separately stored long-term memory."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import re
import sqlite3
import time
import unicodedata
from uuid import uuid4


CATEGORIES = {'semantic':'事实与喜好','episodic':'重要经历','procedural':'沟通偏好',
              'language':'语言习惯','project':'项目进展'}
TYPES = ('explicit','inferred','temporary','uncertain')
TOPICS = {
    '音乐': ('音乐','歌曲','歌单','歌手','听歌','爵士','摇滚','流行','轻音乐','古典','jazz','rock','music','歌'),
    '影视': ('视频','短片','电影','影视','纪录片','动漫','科幻片','恐怖片','动画','剧集','看点什么','b站','哔哩哔哩'),
    '动物': ('小猫','猫咪','小狗','狗狗','柴犬','仓鼠','宠物'),
    '游戏': ('游戏','像素风','像素','game','ps5','steam'),
    '界面': ('界面','主题','ui','颜色','低饱和','紫色','深色','浅色','鲜艳','果冻','布局'),
    '表达': ('解释','回答','提示词','直接','详细','简短','完整','分步骤','可复制','沟通'),
    '桌宠': ('桌宠','提提','live2d','indextts','tt','水手服'),
}
RISK = re.compile(
    r'密码|口令|密钥|秘钥|验证码|支付|银行卡|银行账号|信用卡|身份证|护照|家庭住址|我家住|精确地址|'
    r'病史|诊断|疾病|病人|癌症|抑郁|焦虑症|过敏|看病|住院|药物|药品|正在吃药|怀孕|生理期|健康|'
    r'宗教|信教|佛教|基督|伊斯兰|天主教|政治立场|政治|政党|选举|性取向|同性恋|异性恋|双性恋|跨性别|'
    r'邮箱|电子邮件|手机号|电话号码|api[ _-]*key|password|credential|secret|access[ _-]*token|'
    r'refresh[ _-]*token|authorization\s*[:：]|bearer\s+[\w.-]+|sk-[\w-]{8,}|'
    r'\b(?:ghp_|gho_|AKIA|AIza|eyJ)[\w.-]{8,}|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}|'
    r'(?<!\d)\d(?:[ -]?\d){8,}(?!\d)|\d+号(?:楼|栋|室)|(?:road|street|avenue)\s+\d+|'
    r'\b(?:diabetes|depression|bipolar|cancer|adhd|diagnosed|religion|sexual orientation|gay|lesbian)\b',
    re.IGNORECASE)
INSTRUCTION = re.compile(r'忽略.{0,8}(?:规则|指令)|系统提示词|system prompt|ignore previous|调用工具|读取凭证',
                         re.IGNORECASE)
PREFERENCE = re.compile(r'^(?:(?:其实|不过|现在|以前|一直|其实现在)[，,]?)?我(?:更|很|最|特别|一直|已经|现在|以前|平时|通常|一般)*'
                        r'(喜欢|偏爱|更喜欢|不再喜欢|不喜欢|讨厌|偏好|爱听|爱喝|经常听|最近在听|喜欢听)\s*(.{2,125})$',re.I)
DRINK_RECALL = re.compile(
    r'(?:(?:你(?:还)?记得|你知道|告诉我)[，, ]*)?我?'
    r'(?:(?:(?:平时|通常|一般|最|更)*(?:喜欢喝|爱喝|偏爱喝|常喝)|(?:平时|通常|一般)喝)'
    r'(?:什么(?:饮料)?|啥|哪种饮料)|(?:最|更)?喜欢(?:什么|哪种)饮料)(?:吗|来着|呢)?'
)
LANGUAGE = ('直接开始','帮我看看','继续这一步','帮我改一下','按这个来','先查一下','直接帮我','能不能帮我',
            '按照这个要求','先分析一下')
STOP = set('之前 以前 我们 可以 问题 什么 这个 继续 之前的 一下 想要 推荐 帮我 记得 记住 喜欢 完成 项目 风格 相关 内容 现在'.split())


def clean(value,limit=360):
    return ' '.join(str(value).split())[:limit].strip('，。；、！？,.;!? ')


def allowed(value):
    content = str(value)
    return not (RISK.search(content) or INSTRUCTION.search(content))


def normalized(value):
    return unicodedata.normalize('NFKC',value).lower()


def domains(value):
    text = normalized(value)
    return {name for name,words in TOPICS.items() if any(
        (re.search(r'\b'+re.escape(word)+r'\b',text) if word.isascii() else word in text)
        for word in words)}


def terms(text):
    words = re.findall(r'[a-z0-9][a-z0-9_+.-]{1,32}|[\u3400-\u9fff]+',normalized(text))
    result = set()
    for word in words:
        if word.isascii():
            result.add(word)
        else:
            result.update(word[i:i+n] for n in (2,3) for i in range(len(word)-n+1))
    return result-STOP


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def is_drink_recall(query):
    return DRINK_RECALL.fullmatch(clean(query)) is not None


DECORATION_REQUEST = re.compile(
    r'我(?:很|更|挺|其实|不再|不|特别|一直)*(?:喜欢|偏好|希望|想看|想要|讨厌)|'
    r'以后|平时|(?:聊(?:天)?|说话|回复|回答|打字)(?:时|的时候)|'
    r'不要|别|不用|请|可以|(?:用|发|带|加|配)(?:上|一些|一点|点|几个)?(?=颜文字|表情|emoji)', re.I)
NO_DECORATION = re.compile(r'不再喜欢|不喜欢|讨厌|不希望|不想(?:看|要)|不要|别|不用|不带|不加|不发|禁用')
EMOJI = re.compile(r'emoji|表情符号|小表情(?!包)|(?:发|用|带|加|配)(?:上|一些|一点|点|几个)?表情(?!包)', re.I)
NON_TEXT_EXPRESSION = re.compile(r'表情包|Live2D|脸部|面部|表情动作|切换表情|人物卡', re.I)


def reply_decorations(text):
    """Recognize only the two supported text style preferences or turn overrides."""
    text = re.sub(r'```[\s\S]*?(?:```|\Z)|「[^」]*」|“[^”]*”', '', text[:4000])
    flags = {}
    for part in re.split(r'[。！？!?；;，,\n]|但是|不过|而是', text):
        if not DECORATION_REQUEST.search(part) or re.search(r'^(?:如果|假如|举例)|是什么意思|怎么写颜文字', part.strip()):
            continue
        enabled = not NO_DECORATION.search(part)
        if '颜文字' in part:
            flags['kaomoji'] = enabled
        if not NON_TEXT_EXPRESSION.search(part) and (EMOJI.search(part)
                or re.search(r'颜文字\s*(?:和|与|及|、)\s*表情(?!包)|表情\s*(?:和|与|及|、)\s*颜文字', part)):
            flags['emoji'] = enabled
    return flags


def is_direct_preference(draft):
    return (draft.category == 'semantic' and draft.kind == 'explicit'
            and bool(PREFERENCE.fullmatch(draft.content))
            and not re.match(r'^(?:其实|不过)?(?:以前|原来|之前|我(?:以前|曾经|过去|原来|之前))',draft.content))



@dataclass(frozen=True)
class Draft:
    category: str
    content: str
    kind: str = 'explicit'
    subject: str = ''
    project: str = ''
    importance: float = .65
    replace: bool = False
    excerpt: str = ''


class MemoryExtractor:
    def extract(self,text,*,explicit=False):
        if not text or not allowed(text):
            return []
        matches = []
        for sentence in re.split(r'[。！？!?；;\n]+',text[:6000])[:20]:
            value = clean(sentence)
            if not value or not allowed(value):
                continue
            item = self.classify(value,explicit)
            if item:
                matches.append(replace(item,excerpt=value))
            if not explicit:
                matches.extend(Draft('language',f'用户多次使用「{phrase}」来提出请求', 'inferred',
                                     phrase,importance=.40,excerpt=value)
                               for phrase in LANGUAGE if value.startswith(phrase))
        return matches[:8]

    @staticmethod
    def classify(text,explicit):
        prior = re.match(r'^(?:以前|之前|原来)(?:我)?(?:很)?(?:喜欢|偏爱)(.+)$',text)
        if prior:
            shift = re.search(r'[，,](?:但(?:是)?[，,]?)?(?:现在|如今)(?:我)?(.+)$',text)
            if shift:
                text = '我现在'+shift[1]
            else:
                return Draft('episodic',text,importance=.55)
        uncertainty = re.match(r'^我(?:可能|也许|或许|好像)(?:喜欢|想学|想做)(.{2,120})$',text)
        if uncertainty:
            return Draft('semantic',text,'uncertain',clean(uncertainty[1],100),importance=.48)
        procedural = re.search(r'(提示词|回答|回复|解释|方案|交流|步骤|语气|请你|麻烦你|每次).{0,45}'
                               r'(完整|复制|详细|简短|直接|分阶段|重复询问|先分析|自然|不要|不用)',text)
        prompt_format = '提示词' in text and bool(re.search(r'完整|复制',text))
        if procedural or prompt_format or re.match(r'(以后|今后|从现在起|平时|默认)(?:请|都|回答|先|别|不要|给我)',text):
            ongoing = explicit or bool(re.search(r'以后|今后|平时|每次|一直|都要|长期|默认',text))
            return Draft('procedural',text,'explicit' if ongoing else 'inferred',
                         subject='提示词完整可复制' if prompt_format else '',
                         importance=.76,replace=bool(re.search(r'改成|改为|从现在|以后.{0,10}别',text)))
        project = re.search(r'(?:项目|课题)[「《“"：:\s]*([A-Za-z0-9_+.-]{2,28}|[\u3400-\u9fff]{2,12}?)(?=[」》”"，,:：\s]|的|已经|完成|是|$)|'
                            r'([A-Za-z][A-Za-z0-9_+.-]{1,28})\s*(?:项目|课题)',text,re.I)
        if not project and '桌宠' in text:
            project = re.search(r'(桌宠)',text)
        if project and (explicit or re.search(r'完成|已做|进度|目标|架构|决定|计划|下一步|问题|叫|命名|选了|负责|需要|使用',text)):
            name = next(group for group in project.groups() if group)
            decision = re.search(r'目标|架构|进度|下一步|项目名|命名',text)
            return Draft('project',text,subject=decision[0] if decision else '',project=name,
                         importance=.82,replace=bool(decision or re.search(r'改成|改为|不再|调整',text)))
        preference = PREFERENCE.match(text)
        if preference and not text.endswith('吗'):
            verb,value = preference.groups()
            subject = clean('喝'+value if verb=='爱喝' else value,100)
            if re.search(r'今天|暂时|这一次|这次|这回|这两天|最近',text):
                return Draft('semantic',text,'temporary',subject,importance=.55)
            return Draft('semantic',text,subject=subject,importance=.75,
                         replace=bool(re.search(r'不喜欢|不再|讨厌|更喜欢|改成|改为|现在',text)))
        if re.match(r'我(?:已经|曾经|之前|上次|刚刚|去年|今年)?(?:完成了|遇到过|决定了|选了|参加过|处理了)',text):
            return Draft('episodic',text,importance=.72)
        if re.match(r'我(?:的长期目标|长期计划|平时会|通常会|通常|一直在|经常|每周|每天|习惯|在做|负责|是|来自)',text):
            return Draft('semantic',text,importance=.76)
        if explicit:
            return Draft('semantic',text,importance=.75)
        return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, signature TEXT NOT NULL,
    category TEXT NOT NULL, content TEXT NOT NULL, kind TEXT NOT NULL,
    subject TEXT NOT NULL, project TEXT NOT NULL, confidence REAL NOT NULL,
    importance REAL NOT NULL, status TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL, updated REAL NOT NULL, confirmed REAL NOT NULL, accessed REAL,
    valid_until REAL, access_count INTEGER NOT NULL DEFAULT 0, occurrences INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS memory_find ON memory_items(scope,status,category,signature);
CREATE TABLE IF NOT EXISTS memory_evidence (
    id INTEGER PRIMARY KEY, item TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    session TEXT NOT NULL, message TEXT NOT NULL, excerpt TEXT NOT NULL, observed REAL NOT NULL,
    UNIQUE(item,session,message)
);
CREATE TABLE IF NOT EXISTS memory_relations (
    newer TEXT REFERENCES memory_items(id) ON DELETE CASCADE,
    older TEXT REFERENCES memory_items(id) ON DELETE CASCADE,
    relation TEXT NOT NULL, PRIMARY KEY(newer,older,relation)
);
CREATE TABLE IF NOT EXISTS memory_profile (
    item TEXT PRIMARY KEY REFERENCES memory_items(id) ON DELETE CASCADE,
    scope TEXT NOT NULL, rank REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_jobs (
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, created REAL NOT NULL, finished REAL, status TEXT NOT NULL,
    result INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS memory_history (
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, item TEXT, at REAL NOT NULL, operation TEXT NOT NULL,
    previous TEXT
);
CREATE TABLE IF NOT EXISTS memory_queue (
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, session TEXT NOT NULL, message TEXT NOT NULL,
    observed REAL NOT NULL, draft TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_seen(
    scope TEXT, session TEXT, message TEXT, PRIMARY KEY(scope,session,message)
);
CREATE TABLE IF NOT EXISTS memory_blocks(scope TEXT, topic TEXT, PRIMARY KEY(scope,topic));
"""


class MemoryStore:
    def __init__(self,path):
        self.path = path
        self.extractor = MemoryExtractor()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            try:
                db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(item UNINDEXED, terms)')
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False

    @contextmanager
    def connect(self,*,write=False):
        db = sqlite3.connect(self.path,timeout=.6)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA secure_delete=ON')
            if write:
                db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def get(db,key,default):
        found = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(found[0]) if found else default

    def setting(self,key,default):
        with self.connect() as db:
            return self.get(db,key,default)

    def set_setting(self,key,value):
        with self.connect(write=True) as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value)))

    @staticmethod
    def block_matches(topic,value):
        return (normalized(topic) in normalized(value)
                or topic in TOPICS and topic in domains(value))

    def blocked(self,db,scope,value):
        return any(self.block_matches(row[0],value) for row in db.execute(
            'SELECT topic FROM memory_blocks WHERE scope=?',(scope,)))

    def enqueue(self,scope,session,message,text,observed,*,manual=False):
        drafts = self.extractor.extract(text)
        if not drafts:
            return False
        with self.connect(write=True) as db:
            if not manual and not self.get(db,'auto',True):
                return False
            if observed<=self.get(db,'cutoff:'+scope,0):
                return False
            inserted = db.execute('INSERT OR IGNORE INTO memory_seen VALUES(?,?,?)',
                                  (scope,session,str(message))).rowcount
            if inserted:
                pending = []
                updated = False
                for draft in drafts:
                    if self.blocked(db,scope,draft.content):
                        continue
                    if not manual and is_direct_preference(draft):
                        updated |= self.ingest(db,scope,draft,session,message,observed) is not None
                    else:
                        pending.append(draft)
                if updated:
                    self.rebuild_profile(db,scope)
                db.executemany('INSERT INTO memory_queue(scope,session,message,observed,draft) VALUES(?,?,?,?,?)',
                               ((scope,session,str(message),observed,json.dumps(asdict(draft),ensure_ascii=False))
                                for draft in pending))
                db.execute('DELETE FROM memory_queue WHERE scope=? AND id IN (SELECT id FROM memory_queue '
                           'WHERE scope=? ORDER BY id DESC LIMIT -1 OFFSET 360)',(scope,scope))
            return bool(inserted)

    def recover_queued_preferences(self,scope):
        """Make direct statements saved by older versions available on recall."""
        with self.connect() as db:
            waiting = db.execute('SELECT 1 FROM memory_queue WHERE scope=? LIMIT 1',(scope,)).fetchone()
        if not waiting:
            return
        with self.connect(write=True) as db:
            if not self.get(db,'auto',True):
                return
            updated = False
            rows = db.execute('SELECT * FROM memory_queue WHERE scope=? ORDER BY observed,id',(scope,)).fetchall()
            for row in rows:
                draft = Draft(**json.loads(row['draft']))
                if is_direct_preference(draft):
                    updated |= self.ingest(db,scope,draft,row['session'],row['message'],row['observed']) is not None
                    db.execute('DELETE FROM memory_queue WHERE id=?',(row['id'],))
            if updated:
                self.rebuild_profile(db,scope)

    def audit(self,db,scope,item,operation,previous=None):
        db.execute('INSERT INTO memory_history(scope,item,at,operation,previous) VALUES(?,?,?,?,?)',
                   (scope,item,time.time(),operation,json.dumps(dict(previous),ensure_ascii=False) if previous else None))

    def index(self,db,item,content=None):
        if self.fts:
            db.execute('DELETE FROM memory_fts WHERE item=?',(item,))
            if content:
                db.execute('INSERT INTO memory_fts VALUES(?,?)',(item,' '.join(sorted(terms(content)))))

    def rebuild_profile(self,db,scope):
        db.execute('DELETE FROM memory_profile WHERE scope=?',(scope,))
        db.execute("INSERT INTO memory_profile SELECT id,scope,confidence*importance+pinned FROM memory_items "
                   "WHERE scope=? AND status='active' AND category IN ('semantic','procedural','language') "
                   "AND kind!='temporary' ORDER BY pinned DESC,confidence*importance DESC,confirmed DESC LIMIT 30",(scope,))

    def ingest(self,db,scope,draft,session,message,observed):
        text = clean(draft.content)
        excerpt = clean(draft.excerpt or text)
        if (not text or draft.category not in CATEGORIES or draft.kind not in TYPES
                or not allowed(text+' '+draft.subject+' '+draft.project+' '+excerpt)
                or self.blocked(db,scope,text+' '+excerpt)):
            return None
        subject = clean(draft.subject,100)
        project = clean(draft.project,100)
        keys = terms(subject or text)
        rows = db.execute("SELECT * FROM memory_items WHERE scope=? AND category=? AND project=? COLLATE NOCASE "
                          "AND (kind='temporary')=? AND status IN ('active','candidate') "
                          "ORDER BY pinned DESC,updated DESC LIMIT 120",
                          (scope,draft.category,project,int(draft.kind=='temporary'))).fetchall()
        signature = digest(''.join(ch for ch in normalized(subject or text) if ch.isalnum()))
        match = next((row for row in rows if row['signature']==signature),None)
        if match is None and keys and draft.category in ('semantic','procedural','language'):
            for row in rows:
                other = terms(row['subject'] or row['content'])
                if other and len(keys&other)/len(keys|other)>=.70:
                    match = row
                    break
        if match is None and draft.replace:
            # Different films, animals, or music styles can coexist as preferences.
            domain = domains(text) - {'影视', '动物'}
            if draft.category == 'semantic' and PREFERENCE.fullmatch(text):
                domain.discard('音乐')
            candidates = [r for r in rows if domain & domains(r['content'])
                          and (r['status']=='active' or not r['pinned'])] if domain else []
            if len(candidates)==1:
                match = candidates[0]
        now = time.time()
        expiry = observed+7*86400 if draft.kind=='temporary' else None
        confidence = {'explicit':.95,'inferred':.55,'uncertain':.50,'temporary':.91}[draft.kind]
        active = 'active' if draft.kind in ('explicit','temporary') else 'candidate'
        item = uuid4().hex
        if match is not None:
            if match['status']=='active' and draft.kind=='uncertain':
                return None
            if db.execute('SELECT 1 FROM memory_evidence WHERE item=? AND session=? AND message=?',
                          (match['id'],session,message)).fetchone():
                return match['id']
            if draft.replace and draft.kind=='explicit' and match['content']!=text:
                if observed < match['confirmed']:
                    return None
                self.audit(db,scope,match['id'],'superseded',match)
                db.execute("UPDATE memory_items SET status='archived',updated=? WHERE id=?",(now,match['id']))
                self.index(db,match['id'])
            else:
                item = match['id']
                self.evidence(db,item,session,message,excerpt,observed)
                distinct,amount = db.execute('SELECT COUNT(DISTINCT session),COUNT(*) FROM memory_evidence WHERE item=?',(item,)).fetchone()
                promote = draft.kind=='inferred' and distinct>=2 and amount>=(3 if draft.category=='language' else 2)
                new_conf = max(match['confidence'],.82 if promote else confidence)
                status = 'active' if promote or 'active' in (active,match['status']) else 'candidate'
                self.audit(db,scope,item,'confirmed' if status==match['status'] else 'promoted',match)
                kind = 'explicit' if draft.kind=='explicit' else match['kind']
                db.execute("UPDATE memory_items SET status=?,confidence=?,kind=?,confirmed=MAX(confirmed,?),updated=?,"
                           "occurrences=?,valid_until=CASE WHEN kind='temporary' THEN ? ELSE valid_until END WHERE id=?",
                           (status,new_conf,kind,observed,now,amount,expiry or match['valid_until'],item))
                self.index(db,item,(match['content']+' '+project) if status=='active' else None)
                return item
        db.execute('INSERT INTO memory_items(id,scope,signature,category,content,kind,subject,project,confidence,'
                   'importance,status,created,updated,confirmed,valid_until) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (item,scope,signature,draft.category,text,draft.kind,subject,project,confidence,
                    max(0,min(1,draft.importance)),active,now,now,observed,expiry))
        self.evidence(db,item,session,message,excerpt,observed)
        self.index(db,item,(text+' '+project) if active=='active' else None)
        self.audit(db,scope,item,'created')
        if match is not None:
            db.execute("INSERT INTO memory_relations VALUES(?,?,'supersedes')",(item,match['id']))
        return item

    @staticmethod
    def evidence(db,item,session,message,excerpt,observed):
        db.execute('INSERT OR IGNORE INTO memory_evidence(item,session,message,excerpt,observed) VALUES(?,?,?,?,?)',
                   (item,session,message,clean(excerpt),observed))

    def remember(self,scope,text,session,*,category=None,project=''):
        drafts = ([Draft(category,clean(text),project=clean(project,100),replace=bool(project))]
                  if category and allowed(text) else self.extractor.extract(text,explicit=True))
        with self.connect(write=True) as db:
            results = [self.ingest(db,scope,draft,session,'manual:'+uuid4().hex,time.time()) for draft in drafts]
            self.rebuild_profile(db,scope)
        return sum(item is not None for item in results)

    def reflect(self,scope,*,manual=False):
        with self.connect(write=True) as db:
            if not manual and not self.get(db,'auto',True):
                return 0
            now = time.time()
            job = db.execute("INSERT INTO memory_jobs(scope,created,status) VALUES(?,?,'running')",(scope,now)).lastrowid
            try:
                db.execute('SAVEPOINT batch')
                rows = db.execute('SELECT * FROM memory_queue WHERE scope=? ORDER BY id LIMIT 48',(scope,)).fetchall()
                changes = 0
                for row in rows:
                    item = self.ingest(db,scope,Draft(**json.loads(row['draft'])),
                                       row['session'],row['message'],row['observed'])
                    changes += bool(item)
                    db.execute('DELETE FROM memory_queue WHERE id=?',(row['id'],))
                expired = db.execute("SELECT * FROM memory_items WHERE scope=? AND status='active' AND valid_until<=?",
                                     (scope,now)).fetchall()
                for row in expired:
                    self.audit(db,scope,row['id'],'expired',row)
                    db.execute("UPDATE memory_items SET status='archived',updated=? WHERE id=?",(now,row['id']))
                    self.index(db,row['id'])
                self.rebuild_profile(db,scope)
                db.execute('RELEASE batch')
                db.execute("UPDATE memory_jobs SET finished=?,status='done',result=? WHERE id=?",(time.time(),changes,job))
                return changes
            except (sqlite3.Error,ValueError,TypeError,KeyError) as error:
                db.execute('ROLLBACK TO batch')
                db.execute('RELEASE batch')
                db.execute("UPDATE memory_jobs SET finished=?,status='failed',error=? WHERE id=?",
                           (time.time(),type(error).__name__,job))
                return -1

    def queued(self,scope):
        with self.connect() as db:
            return db.execute('SELECT COUNT(*) FROM memory_queue WHERE scope=?',(scope,)).fetchone()[0]

    def rows(self,scope,category='',query='',*,limit=600,offset=0):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM memory_items WHERE scope=? AND (?='' OR category=?) "
                "AND (?='' OR instr(lower(content||' '||project),lower(?))>0) "
                "ORDER BY pinned DESC,updated DESC LIMIT ? OFFSET ?",
                (scope,category,category,query,query,limit,offset))]

    def details(self,scope,item):
        with self.connect() as db:
            row = db.execute('SELECT * FROM memory_items WHERE scope=? AND id=?',(scope,item)).fetchone()
            if row is None:
                return [],[]
            return ([dict(value) for value in db.execute('SELECT * FROM memory_evidence WHERE item=? ORDER BY observed DESC LIMIT 100',(item,))],
                    [dict(value) for value in db.execute('SELECT * FROM memory_history WHERE scope=? AND item=? ORDER BY id DESC LIMIT 100',(scope,item))])

    def edit(self,scope,item,text,category,project,pin,*,restore=False):
        text,project = clean(text),clean(project,100)
        if not text or not allowed(text+' '+project) or category not in CATEGORIES:
            return False
        classified = self.extractor.classify(text,True)
        subject = clean(classified.subject,100) if classified.category==category else ''
        signature = digest(''.join(ch for ch in normalized(subject or text) if ch.isalnum()))
        with self.connect(write=True) as db:
            row = db.execute('SELECT * FROM memory_items WHERE scope=? AND id=?',(scope,item)).fetchone()
            if row is None or self.blocked(db,scope,text):
                return False
            self.audit(db,scope,item,'restored' if restore else 'edited',row)
            change = (text,category,project) != (row['content'],row['category'],row['project'])
            if change or restore:
                older = db.execute("SELECT * FROM memory_items WHERE scope=? AND category=? AND id!=? "
                                   "AND signature=? AND project=? AND status='active'",
                                   (scope,category,item,signature,project)).fetchall()
                if restore:
                    older += db.execute("WITH RECURSIVE chain(id) AS (SELECT newer FROM memory_relations WHERE older=? "
                                        "UNION SELECT r.newer FROM memory_relations r JOIN chain c ON r.older=c.id) "
                                        "SELECT m.* FROM chain c JOIN memory_items m ON c.id=m.id "
                                        "WHERE m.scope=? AND m.status='active' AND m.id!=?",(item,scope,item)).fetchall()
                for record in {entry['id']:entry for entry in older}.values():
                    self.audit(db,scope,record['id'],'superseded',record)
                    db.execute("UPDATE memory_items SET status='archived' WHERE id=?",(record['id'],))
                    self.index(db,record['id'])
                db.execute("UPDATE memory_items SET content=?,category=?,project=?,subject=?,signature=?,"
                           "kind='explicit',confidence=1,status='active',valid_until=NULL,pinned=?,updated=? WHERE id=?",
                           (text,category,project,subject,signature,int(pin),time.time(),item))
                self.index(db,item,text+' '+project)
            else:
                db.execute('UPDATE memory_items SET pinned=?,updated=? WHERE id=?',(int(pin),time.time(),item))
            self.rebuild_profile(db,scope)
            return True

    def remove(self,db,scope,item):
        self.index(db,item)
        db.execute('DELETE FROM memory_history WHERE scope=? AND item=?',(scope,item))
        db.execute('DELETE FROM memory_items WHERE scope=? AND id=?',(scope,item))
        self.audit(db,scope,item,'deleted')

    def delete(self,scope,item):
        with self.connect(write=True) as db:
            self.remove(db,scope,item)
            self.rebuild_profile(db,scope)

    def forget(self,scope,topic):
        topic = clean(topic,100)
        if len(topic)<2 or not allowed(topic):
            return 0
        with self.connect(write=True) as db:
            db.execute('INSERT OR IGNORE INTO memory_blocks VALUES(?,?)',(scope,topic))
            rows = db.execute('SELECT id,content,project FROM memory_items WHERE scope=?',(scope,)).fetchall()
            targets = [r['id'] for r in rows if self.block_matches(topic,r['content']+' '+r['project'])]
            revisions = db.execute('SELECT DISTINCT item,previous FROM memory_history WHERE scope=? AND previous IS NOT NULL',
                                   (scope,)).fetchall()
            for revision in revisions:
                if self.block_matches(topic,revision['previous']):
                    db.execute('UPDATE memory_history SET previous=NULL WHERE scope=? AND item=?',
                               (scope,revision['item']))
            for item in targets:
                self.remove(db,scope,item)
            evidence = db.execute('SELECT e.id,e.excerpt FROM memory_evidence e JOIN memory_items m ON m.id=e.item '
                                  'WHERE m.scope=?',(scope,)).fetchall()
            db.executemany('DELETE FROM memory_evidence WHERE id=?',
                           ((row['id'],) for row in evidence if self.block_matches(topic,row['excerpt'])))
            for row in db.execute('SELECT id,draft FROM memory_queue WHERE scope=?',(scope,)).fetchall():
                if self.block_matches(topic,row['draft']):
                    db.execute('DELETE FROM memory_queue WHERE id=?',(row['id'],))
            self.rebuild_profile(db,scope)
            return len(targets)

    def clear(self,scope):
        with self.connect(write=True) as db:
            for row in db.execute('SELECT id FROM memory_items WHERE scope=?',(scope,)).fetchall():
                self.index(db,row['id'])
            for table in ('memory_items','memory_jobs','memory_history','memory_seen','memory_queue','memory_blocks'):
                db.execute(f'DELETE FROM {table} WHERE scope=?',(scope,))
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('cutoff:'+scope,json.dumps(time.time())))
            self.audit(db,scope,None,'cleared')

    def export_data(self,scope):
        with self.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM memory_items WHERE scope=? ORDER BY created,id',(scope,))]
            for row in rows:
                row.pop('scope')
                row['evidence'] = [dict(value) for value in db.execute(
                    'SELECT session,message,excerpt,observed FROM memory_evidence WHERE item=?',(row['id'],))]
            revisions = [dict(row) for row in db.execute(
                'SELECT item,at,operation,previous FROM memory_history WHERE scope=? AND previous IS NOT NULL',(scope,))]
            relations = [dict(row) for row in db.execute(
                'SELECT r.* FROM memory_relations r JOIN memory_items m ON m.id=r.newer WHERE m.scope=?',(scope,))]
            return {'version':1,'items':rows,'revisions':revisions,'relations':relations,
                    'blocks':[row[0] for row in db.execute('SELECT topic FROM memory_blocks WHERE scope=?',(scope,))]}

    def import_data(self,scope,data):
        if (not isinstance(data,dict) or data.get('version')!=1
                or not isinstance(data.get('items'),list) or len(data['items'])>2000
                or not isinstance(data.get('blocks',[]),list) or len(data.get('blocks',[]))>2000):
            raise ValueError('备份格式不受支持')
        with self.connect(write=True) as db:
            for topic in data.get('blocks',[]):
                if isinstance(topic,str) and len(clean(topic,100))>=2 and allowed(topic):
                    db.execute('INSERT OR IGNORE INTO memory_blocks VALUES(?,?)',(scope,clean(topic,100)))
            count = 0
            mapping = {}
            for row in data['items']:
                if not isinstance(row,dict):
                    continue
                if row.get('category') not in CATEGORIES or row.get('kind') not in TYPES:
                    continue
                text,subject,project = (clean(row.get(k,''),360 if k=='content' else 100)
                                        for k in ('content','subject','project'))
                if not text or not allowed(text+' '+subject+' '+project) or self.blocked(db,scope,text):
                    continue
                duplicate = db.execute('SELECT id FROM memory_items WHERE scope=? AND category=? AND content=? AND project=?',
                                       (scope,row['category'],text,project)).fetchone()
                old_id = row.get('id')
                if duplicate:
                    if isinstance(old_id,str):
                        mapping[old_id] = duplicate[0]
                    continue
                now = time.time()
                def number(key,default,low,high):
                    value = row.get(key,default)
                    return max(low,min(high,value)) if isinstance(value,(int,float)) and math.isfinite(value) else default
                kind = row['kind']
                status = row.get('status','candidate')
                status = status if status in ('active','candidate','archived') else 'candidate'
                confidence = number('confidence',.5,0,1)
                if confidence<.75 and status=='active':
                    status = 'candidate'
                item = uuid4().hex
                if isinstance(old_id,str):
                    mapping[old_id] = item
                signature = digest(''.join(ch for ch in normalized(subject or text) if ch.isalnum()))
                if status=='active' and db.execute("SELECT 1 FROM memory_items WHERE scope=? AND category=? "
                                                   "AND project=? AND signature=? AND status='active'",
                                                   (scope,row['category'],project,signature)).fetchone():
                    status = 'archived'
                db.execute('INSERT INTO memory_items(id,scope,signature,category,content,kind,subject,project,'
                           'confidence,importance,status,pinned,created,updated,confirmed,valid_until) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (item,scope,signature,
                            row['category'],text,kind,subject,project,confidence,
                            number('importance',.65,0,1),status,int(row.get('pinned') in (True,1)),
                            number('created',now,0,now),now,number('confirmed',now,0,now),
                            number('valid_until',now+86400,0,now+7*86400) if kind=='temporary' else None))
                evidence = row.get('evidence',[])
                if isinstance(evidence,list):
                    for record in evidence[:100]:
                        if (isinstance(record,dict) and allowed(' '.join(
                                str(record.get(key,'')) for key in ('session','message','excerpt')))):
                            timestamp = record.get('observed',now)
                            timestamp = timestamp if isinstance(timestamp,(int,float)) and 0<=timestamp<=now else now
                            self.evidence(db,item,clean(record.get('session','导入'),120),
                                          clean(record.get('message',''),120),record.get('excerpt',''),timestamp)
                self.index(db,item,(text+' '+project) if status=='active' else None)
                self.audit(db,scope,item,'imported')
                count += 1
            relations = data.get('relations')
            if isinstance(relations,list):
                for row in relations[:4000]:
                    if (isinstance(row,dict) and row.get('relation')=='supersedes'
                            and isinstance(row.get('newer'),str) and isinstance(row.get('older'),str)):
                        a,b = mapping.get(row['newer']),mapping.get(row['older'])
                        if a and b and a!=b:
                            db.execute("INSERT OR IGNORE INTO memory_relations VALUES(?,?,'supersedes')",(a,b))
            revisions = data.get('revisions')
            if isinstance(revisions,list):
                for row in revisions[:6000]:
                    if not isinstance(row,dict) or not isinstance(row.get('item'),str) or row['item'] not in mapping:
                        continue
                    try:
                        previous = json.loads(row.get('previous',''))
                    except (TypeError,ValueError):
                        continue
                    if not isinstance(previous,dict) or previous.get('category') not in CATEGORIES:
                        continue
                    snapshot = {key:clean(previous.get(key,'')) for key in ('content','category','project')}
                    if (not snapshot['content'] or not allowed(' '.join(snapshot.values()))
                            or self.blocked(db,scope,snapshot['content'])):
                        continue
                    timestamp = row.get('at',time.time())
                    if not isinstance(timestamp,(int,float)) or not 0<=timestamp<=time.time():
                        timestamp = time.time()
                    db.execute("INSERT INTO memory_history(scope,item,at,operation,previous) VALUES(?,?,?,'imported revision',?)",
                               (scope,mapping[row['item']],timestamp,json.dumps(snapshot,ensure_ascii=False)))
            self.rebuild_profile(db,scope)
            return count


class MemoryReflector:
    def __init__(self,store):
        self.store = store

    def run(self,scope,*,manual=False):
        return self.store.reflect(scope,manual=manual)


class MemoryRetriever:
    def __init__(self,store):
        self.store = store

    def media_preferences(self, scope, kind):
        topics = {'video': {'影视', '动物'}, 'music': {'音乐'}}.get(kind)
        if not topics:
            return (), ()
        artists = set()
        if kind == 'music':
            from tt_share_content import JAPANESE_TRACKS, POP_TRACKS

            artists = {normalized(track[1]) for track in POP_TRACKS + JAPANESE_TRACKS}
        with self.store.connect() as db:
            if not self.store.get(db, 'chat:' + scope, False):
                return (), ()
            rows = db.execute(
                "SELECT content FROM memory_items WHERE scope=? AND category='semantic' "
                "AND status='active' AND kind='explicit' AND confidence>=.75 "
                "AND (valid_until IS NULL OR valid_until>?) ORDER BY confirmed DESC,updated DESC LIMIT 80",
                (scope, time.time()))
            choices = {}
            for (content,) in rows:
                match = PREFERENCE.fullmatch(content)
                if (not match or not allowed(content)
                        or re.match(r'^(?:其实)?(?:以前|原来|之前|我(?:以前|曾经|过去|原来|之前))', content)
                        or self.store.blocked(db, scope, content)):
                    continue
                verb, text = match.groups()
                listening = (kind == 'music' and (verb == '爱听' or text.startswith('听'))
                             and not re.search(r'说话|语音|播报|故事|听书|播客|听雨', content))
                if not listening and normalized(text) not in artists and not topics & domains(content):
                    continue
                text = re.sub(r'^(?:刷|看|听)(?=.{2,})', '', clean(text, 56))
                text = re.sub(r'(?:的?(?:视频|短片)|的?歌曲|的(?:歌|音乐))$', '', text).strip()
                if len(text) >= 2 and text not in ('这些', '那种', '看视频', '听音乐'):
                    choices.setdefault(normalized(text), (text, verb in ('不再喜欢', '不喜欢', '讨厌')))
            preferred = tuple(text for text, avoid in choices.values() if not avoid)
            avoided = tuple(text for text, avoid in choices.values() if avoid)
            return preferred[:4], avoided[:8]

    def conversation_preferences(self, scope):
        """Keep a few explicit speaking preferences available across topics."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT content FROM memory_items WHERE scope=? AND category='procedural' "
                "AND status='active' AND kind='explicit' AND confidence>=.75 "
                "AND (valid_until IS NULL OR valid_until>?) ORDER BY confirmed DESC,updated DESC LIMIT 40",
                (scope, time.time()))
            return [text for (text,) in rows
                    if re.search(r'聊天|闲聊|说话|语气|口吻|称呼|打招呼|追问|反问', text)
                    and not re.search(r'代码|报告|文件|论文|日志|表格|提示词', text)
                    and allowed(text) and not self.store.blocked(db, scope, text)][:3]

    def decoration_preferences(self, scope):
        """Read durable chat preferences even during a conversation on another topic."""
        found = {}
        with self.store.connect() as db:
            if not self.store.get(db, 'chat:' + scope, False):
                return found
            rows = db.execute(
                "SELECT content FROM memory_items WHERE scope=? AND status='active' AND confidence>=.75 "
                "AND category IN ('semantic','procedural') AND kind!='temporary' "
                "AND (valid_until IS NULL OR valid_until>?) "
                "AND (instr(content,'颜文字')>0 OR instr(content,'表情')>0 OR instr(lower(content),'emoji')>0) "
                "ORDER BY confirmed DESC,updated DESC,id LIMIT 80", (scope, time.time()))
            for row in rows:
                content = row['content']
                if allowed(content) and not self.store.blocked(db, scope, content):
                    for key, enabled in reply_decorations(content).items():
                        found.setdefault(key, enabled)
                if len(found) == 2:
                    break
        return found

    def search(self,scope,query,category='',limit=6):
        if not allowed(query):
            return []
        query_terms = terms(query)
        query_domains = domains(query)
        drink_recall = is_drink_recall(query)
        if not query_terms and not query_domains and not drink_recall:
            return []
        now = time.time()
        with self.store.connect() as db:
            if self.store.blocked(db,scope,query):
                return []
            indexed = set()
            if self.store.fts and query_terms:
                request = ' OR '.join('"'+word+'"' for word in sorted(query_terms,key=len,reverse=True)[:56])
                indexed = {row[0] for row in db.execute(
                    'SELECT memory_fts.item FROM memory_fts JOIN memory_items m ON m.id=memory_fts.item '
                    'WHERE memory_fts MATCH ? AND m.scope=? ORDER BY bm25(memory_fts) LIMIT 120',(request,scope))}
            rows = db.execute("SELECT * FROM memory_items WHERE scope=? AND status='active' "
                              "AND (?='' OR category=?) AND confidence>=.75 AND (valid_until IS NULL OR valid_until>?) "
                              "ORDER BY pinned DESC,confirmed DESC LIMIT 600",(scope,category,category,now)).fetchall()
            missing = indexed-{row['id'] for row in rows}
            if missing:
                placeholders = ','.join('?' for _ in missing)
                rows.extend(db.execute("SELECT * FROM memory_items WHERE scope=? AND status='active' "
                                       "AND (?='' OR category=?) AND confidence>=.75 "
                                       f"AND (valid_until IS NULL OR valid_until>?) AND id IN ({placeholders})",
                                       (scope,category,category,now,*missing)).fetchall())
            ranked = []
            projects = {normalized(row['project']) for row in rows
                        if row['project'] and normalized(row['project']) in normalized(query)}
            for row in rows:
                content = row['content']+' '+row['project']
                if drink_recall and not (
                    row['category']=='semantic' and row['kind']=='explicit'
                    and row['subject'].startswith('喝') and PREFERENCE.fullmatch(row['content'])
                ):
                    continue
                overlap = query_terms & terms(content)
                domain = query_domains & domains(content)
                same_project = bool(row['project'] and normalized(row['project']) in normalized(query))
                if projects and row['category']=='project' and not same_project:
                    continue
                if self.store.blocked(db,scope,content):
                    continue
                if not overlap and not domain and not same_project and not drink_recall:
                    continue
                if len(overlap)==1 and len(next(iter(overlap)))<3 and not domain and not same_project and not drink_recall:
                    continue
                freshness = 1/(1+max(0,now-row['confirmed'])/(30*86400))
                score = (2*len(overlap)/(1+len(query_terms)**.5) + 1.3*bool(domain)
                         + 6*same_project + .25*(row['id'] in indexed)
                         + .25*row['importance'] + .18*freshness + .1*row['pinned']
                         + .04*math.log1p(min(row['access_count'],25)))
                ranked.append((score,row))
            ranked.sort(key=lambda entry:entry[0],reverse=True)
            return [dict(row) for _,row in ranked[:limit]]

    def context(self,scope,query,budget=1000,*,results=None):
        if not allowed(query):
            return []
        with self.store.connect() as db:
            if self.store.blocked(db,scope,query):
                return []
        results = self.search(scope,query) if results is None else list(results)
        if self.store.setting('stable:'+scope,False):
            with self.store.connect() as db:
                profile = db.execute("SELECT m.* FROM memory_profile p JOIN memory_items m ON m.id=p.item "
                                     "WHERE p.scope=? AND m.category='procedural' AND m.pinned=1 AND m.status='active' "
                                     "ORDER BY p.rank DESC LIMIT 2",(scope,)).fetchall()
                already = {r['id'] for r in results}
                results.extend(dict(row) for row in profile if row['id'] not in already
                               and allowed(row['content']) and not self.store.blocked(db,scope,row['content']))
        items = []
        flags = []
        for row in results:
            entry = {'类别':CATEGORIES[row['category']],'摘要':row['content'],
                     '依据':'用户明确表达' if row['kind']=='explicit' else '历史观察',
                     '时间':time.strftime('%Y-%m-%d',time.localtime(row['confirmed']))}
            if len(json.dumps([*items,entry],ensure_ascii=False))>budget:
                break
            flags.append((time.time(),row['id'],scope))
            items.append(entry)
        if flags:
            with self.store.connect(write=True) as db:
                db.executemany('UPDATE memory_items SET accessed=?,access_count=access_count+1 WHERE id=? AND scope=?',flags)
        return items
