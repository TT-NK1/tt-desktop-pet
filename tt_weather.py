"""Weather requests, cached forecasts and reminders for the desktop pet."""
from datetime import datetime, timedelta
import json
import math
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, build_opener

from PySide6.QtCore import QCoreApplication, QLocationPermission, QObject, Qt, QTimer, Signal


DEFAULTS = dict(enabled=True, interval=30, location=None, auto=True, voice=True,
                summary='08:00', quiet_start='22:00', quiet_end='07:00',
                depart_enabled=False, depart='08:30', rain=65, hot=34, cold=6)
CURRENT = 'temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m'
HOURLY = 'temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m'
DAILY = 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max'


def number(row, name):
    value = row.get(name)
    return value if type(value) in (int,float) and math.isfinite(value) else None


def label(code):
    if code == 0:
        return '☀', '晴'
    if code in (1,2,3):
        return '☁', '多云' if code != 3 else '阴'
    if code in (45,48):
        return '≋', '雾'
    if code in (95,96,97,99):
        return '⛈', '雷雨'
    if code in (71,73,75,77,85,86):
        return '❄', '雪'
    if code in (51,53,55,56,57,61,63,65,66,67,80,81,82):
        return '☂', '降雨'
    return '·', '天气状况未知'


def format_value(value, unit=''):
    return f'{value:g}{unit}' if value is not None else '—'


def rows(section, fields):
    return [dict(time=value, **{key: section[key][i] if isinstance(section.get(key),list)
                and i<len(section[key]) else None for key in fields.split(',')})
            for i,value in enumerate(section.get('time',[])) if isinstance(value,str)]


def forecasts(data):
    current = data['current']
    now = datetime.fromisoformat(current['time'])
    hour = now.replace(minute=0,second=0,microsecond=0)
    hourly = rows(data.get('hourly',{}),HOURLY)
    daily = rows(data.get('daily',{}),DAILY)
    return current, [row for row in hourly if row['time']>=hour.isoformat(timespec='minutes')][:24], daily[:3]


def forecast_url(location):
    return 'https://api.open-meteo.com/v1/forecast', dict(
        latitude=location['latitude'],longitude=location['longitude'],timezone='auto',
        forecast_days=4,current=CURRENT,hourly=HOURLY,daily=DAILY,wind_speed_unit='kmh')


def matches_rain(row, threshold):
    probability = number(row,'precipitation_probability')
    amount = number(row,'precipitation')
    return probability is not None and probability>=threshold and amount is not None and amount>=.2


def summary(data, cfg):
    current, hourly, daily = forecasts(data)
    today = daily[0] if daily else {}
    icon, name = label(number(today or current,'weather_code'))
    high, low = number(today,'temperature_2m_max'),number(today,'temperature_2m_min')
    text = f'今天预计{name}。' if name!='天气状况未知' else '今天的预报更新了。'
    if high is not None and low is not None:
        text += f'最高约{high:.0f}度，最低约{low:.0f}度。'
    rain = next((row for row in hourly if row['time'][:10]==current['time'][:10]
                 and matches_rain(row,cfg['rain'])),None)
    probability = number(today,'precipitation_probability_max')
    if rain:
        text += f'{int(rain["time"][11:13])}点前后可能下雨，出门记得带伞哦。'
    elif probability is not None:
        text += f'今天最高降雨概率约{probability:.0f}%。'
    if high is not None and high>=cfg['hot']:
        text += '出门记得防晒，多喝点水哦。'
    elif low is not None and low<=cfg['cold']:
        text += '早晚记得穿暖一点哦。'
    return icon,text


def insight(data, cfg, now):
    current, hourly, daily = forecasts(data)
    conditions = [current,*hourly[:3]]
    today = daily[0] if daily else {}
    clock = datetime.fromisoformat(current['time'])
    depart = datetime.combine(now.date(),datetime.strptime(cfg['depart'],'%H:%M').time())
    if cfg['depart_enabled'] and timedelta(0)<=depart-now<=timedelta(hours=1):
        limit = clock+timedelta(hours=1)
        if any(datetime.fromisoformat(row['time'])<=limit and matches_rain(row,cfg['rain']) for row in hourly):
            yield 'rain','☂','未来一小时可能下雨，出门记得带伞哦。'
    if any(number(row,'weather_code') in (95,96,97,99) for row in conditions):
        yield 'storm','⛈','预报附近可能有雷雨，出门前再看看天气哦。'
    if label(number(current,'weather_code'))[0]=='☂':
        yield 'rain','☂','当前天气模型显示有雨，出门记得带伞呀。'
    elif any(matches_rain(row,cfg['rain']) for row in hourly[:3]):
        yield 'rain','☂','接下来的几小时可能下雨，出门记得带伞哦。'
    if any((number(row,'wind_speed_10m') or 0)>=40 for row in conditions):
        yield 'wind','≋','预报这会儿风比较大，出门留意一下哦。'
    high, low = number(today,'temperature_2m_max'),number(today,'temperature_2m_min')
    feels = number(current,'apparent_temperature')
    if any(value is not None and value>=cfg['hot'] for value in (high,feels)):
        yield 'hot','☀','今天预计比较热，出门注意防晒，记得多喝水哦。'
    if any(value is not None and value<=cfg['cold'] for value in (low,feels)):
        yield 'cold','❄','今天有些时段会比较冷，出门记得穿暖一点。'
    if high is not None and low is not None and high-low>=12:
        yield 'swing','☀','今天预报的早晚温差比较大，记得根据温度加减衣服。'


def quiet(now, cfg):
    start,end = cfg['quiet_start'],cfg['quiet_end']
    hhmm = now.strftime('%H:%M')
    return start<=hhmm<end if start<=end else hhmm>=start or hhmm<end


class WeatherService(QObject):
    changed = Signal()
    results = Signal(list,str)
    location_failed = Signal(str)
    reminder = Signal(str,str,bool)
    response = Signal(object,object,str,bool)

    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store
        self.cfg = DEFAULTS | store.get_setting('weather_settings',{})
        self.cache = store.get_setting('weather_cache',{})
        self.delivered = store.get_setting('weather_delivered',{})
        self.forecast_request = False
        self.search_request = False
        self.location_source = None
        self.locating = False
        self.closed = False
        self.error = ''
        self.last_attempt = float('-inf')
        self.retry_after = 0.
        self.last_notice = float('-inf')
        self.conditions = None
        self.pending = {}
        self.busy = lambda: False
        self.response.connect(self.finish_request,Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self, interval=15000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        QTimer.singleShot(0,self,self.tick)

    @property
    def data(self):
        return self.cache.get('data') if self.cache.get('location')==self.cfg['location'] else None

    @property
    def updated(self):
        return self.cache.get('updated',0) if self.data else 0

    def set(self,key,value):
        self.cfg[key] = value
        self.store.save_setting('weather_settings',self.cfg)
        self.changed.emit()

    def select(self,location):
        self.last_attempt = float('-inf')
        self.error = ''
        self.pending.clear()
        self.conditions = None
        self.set('location',location)
        self.refresh()

    def request(self, endpoint, fields, finished):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):
                return None

        def run():
            value,error,limited = None,'',False
            url = endpoint+'?'+urlencode(fields,safe=',')
            for attempt in range(2):
                try:
                    with build_opener(NoRedirect()).open(url,timeout=9) as reply:
                        value = json.loads(reply.read(1_000_000))
                    if not isinstance(value,dict) or value.get('error'):
                        raise ValueError('weather response')
                    error = ''
                    break
                except HTTPError as exc:
                    limited = exc.code==429
                    error = '天气服务暂时限流，请稍后重试。' if limited else '天气服务暂不可用，请稍后重试。'
                    if exc.code not in (502,503,504):
                        break
                except OSError:
                    error = '暂时无法连接天气服务。请检查网络后重试。'
                except (ValueError,TypeError):
                    error = '天气服务返回了无效数据。'
                    break
                if self.closed:
                    return
                if attempt==0:
                    time.sleep(.4)
            if not self.closed:
                self.response.emit(finished,None if error else value,error,limited)
        Thread(target=run,name='TT Weather',daemon=True).start()
        return True

    def finish_request(self, finished, value, error, limited):
        if not self.closed:
            finished(value,error,limited)

    def search(self,text):
        if self.closed:
            return
        name,_,country = text.strip().partition(',')
        if len(name.strip())<2:
            self.results.emit([],'请输入至少两个字符的城市或地区。')
            return
        if self.search_request:
            return
        fields = dict(name=name.strip(),language='zh',count=12)
        if len(country.strip())==2 and country.strip().isascii() and country.strip().isalpha():
            fields['countryCode'] = country.strip().upper()
        def done(value,error,_):
            self.search_request = None
            places = []
            for row in (value or {}).get('results',[]):
                if number(row,'latitude') is None or number(row,'longitude') is None:
                    continue
                parts = dict.fromkeys(row.get(key) for key in ('name','admin1','country') if row.get(key))
                places.append(dict(name=' · '.join(parts),latitude=row['latitude'],longitude=row['longitude']))
            self.results.emit(places,error or ('' if places else '没有匹配的城市。可试拼音或英文名。'))
        self.search_request = self.request('https://geocoding-api.open-meteo.com/v1/search',fields,done)

    def refresh(self):
        location = self.cfg['location']
        if self.closed or not self.cfg['enabled'] or not location or self.forecast_request or time.monotonic()<self.retry_after:
            return
        if time.monotonic()-self.last_attempt<5:
            return
        self.last_attempt = time.monotonic()
        def done(value,error,limited):
            self.forecast_request = None
            if limited:
                self.retry_after = time.monotonic()+300
            if location!=self.cfg['location']:
                self.refresh()
                return
            try:
                if value:
                    current,hourly,daily = forecasts(value)
                    if not hourly or not daily or number(current,'temperature_2m') is None:
                        raise ValueError('forecast incomplete')
            except (KeyError,TypeError,ValueError,AttributeError):
                value,error = None,'天气数据不完整。'
            if value:
                self.cache = dict(location=location,data=value,updated=time.time())
                self.store.save_setting('weather_cache',self.cache)
            self.error = error
            self.changed.emit()
            if value:
                self.evaluate()
        self.forecast_request = self.request(*forecast_url(location),done)
        self.changed.emit()

    def tick(self):
        if not self.cfg['enabled']:
            return
        if time.monotonic()-self.last_attempt>=self.cfg['interval']*60:
            self.refresh()
        self.evaluate()

    def evaluate(self, now=None):
        cfg = self.cfg
        if not cfg['enabled'] or not cfg['auto'] or self.error or not self.data or time.time()-self.updated>5400:
            return
        now = now or datetime.now()
        day = now.date().isoformat()
        active = dict((key,(icon,text)) for key,icon,text in reversed(list(insight(self.data,cfg,now))))
        if self.conditions is None:
            self.conditions = set(active)
            for key in ('rain','storm','wind','hot','cold'):
                if key in active:
                    self.pending[key] = active[key]
        else:
            for key in active.keys()-self.conditions:
                self.pending[key] = active[key]
            self.conditions = set(active)
        for key in self.pending.keys()-active.keys():
            del self.pending[key]
        for key in self.pending.keys() & active.keys():
            self.pending[key] = active[key]
        if quiet(now,cfg) or self.busy():
            return
        if time.monotonic()-self.last_notice<2700:
            return
        due = datetime.combine(now.date(),datetime.strptime(cfg['summary'],'%H:%M').time())
        choice = next((key for key in ('storm',) if key in self.pending and self.delivered.get(key)!=day),None)
        if not choice and due<=now<due+timedelta(hours=5) and self.delivered.get('summary')!=day:
            choice = 'summary'
        if not choice:
            choice = next((key for key in ('rain','wind','hot','cold','swing') if key in self.pending and self.delivered.get(key)!=day),None)
        if not choice:
            return
        icon,text = summary(self.data,cfg) if choice=='summary' else self.pending.pop(choice)
        self.delivered[choice] = day
        if choice=='summary' and '带伞' in text:
            self.delivered['rain'] = day
        if choice=='summary' and '防晒' in text:
            self.delivered['hot'] = day
        if choice=='summary' and '穿暖' in text:
            self.delivered['cold'] = day
        self.last_notice = time.monotonic()
        self.store.save_setting('weather_delivered',self.delivered)
        self.reminder.emit(icon,text,cfg['voice'])

    def locate(self):
        from PySide6.QtPositioning import QGeoPositionInfoSource

        if self.locating or self.closed:
            return
        self.locating = True
        initial = self.cfg['location']

        def finish(position=None):
            source, self.location_source = self.location_source, None
            if source:
                source.deleteLater()
            self.locating = False
            if self.closed or self.cfg['location']!=initial:
                return
            if position is None or not position.coordinate().isValid():
                self.location_failed.emit('Windows 定位未获授权或暂不可用，可手动搜索城市。')
                return
            point = position.coordinate()
            self.select(dict(name='Windows 当前位置（约略）',latitude=round(point.latitude(),1),
                             longitude=round(point.longitude(),1)))

        def authorized(status):
            if self.closed or not self.locating:
                return
            if status!=Qt.PermissionStatus.Granted:
                finish()
                return
            source = QGeoPositionInfoSource.createSource('winrt',self)
            if source is None:
                finish()
                return
            self.location_source = source
            source.positionUpdated.connect(lambda point: finish(point) if self.location_source is source else None)
            source.errorOccurred.connect(lambda _:finish() if self.location_source is source else None)
            source.requestUpdate(15000)

        permission = QLocationPermission()
        permission.setAccuracy(QLocationPermission.Accuracy.Approximate)
        app = QCoreApplication.instance()
        status = app.checkPermission(permission)
        if status==Qt.PermissionStatus.Undetermined:
            app.requestPermission(permission,lambda result:authorized(result.status()))
        else:
            authorized(status)

    def close(self):
        self.closed = True
        self.timer.stop()
        if self.location_source:
            self.location_source.deleteLater()
            self.location_source = None
