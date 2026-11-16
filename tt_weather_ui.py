"""The outfit-themed forecast bubble and TT's weather settings."""
from datetime import datetime
from math import cos, pi, sin
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTime, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from tt_bubble import bubble_placement, tail_direction
from tt_bubble_art import outline, paint_glass, paint_motif, palette_for
from tt_weather import forecasts, format_value, insight, label, number


def button(text, callback):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    return widget


def plain(text=''):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


class WeatherSettings(QScrollArea):
    def __init__(self, service, parent):
        super().__init__(parent)
        self.service = service
        self.setWidgetResizable(True)
        page = QWidget(self)
        self.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24,20,24,20)
        layout.setSpacing(11)
        heading = plain('天气设置')
        heading.setStyleSheet('font-size:21px;font-weight:700')
        layout.addWidget(heading)
        self.place = plain()
        layout.addWidget(self.place)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('城市、拼音或英文，例如 Tokyo, JP')
        self.query.setAccessibleName('搜索城市或地区')
        row.addWidget(self.query,1)
        search = lambda: service.search(self.query.text())
        self.query.returnPressed.connect(search)
        row.addWidget(button('搜索城市',search))
        layout.addLayout(row)
        row = QHBoxLayout()
        self.choices = QComboBox()
        self.choices.setAccessibleName('匹配城市')
        row.addWidget(self.choices,1)
        self.save = button('保存城市',self.save_city)
        self.save.setEnabled(False)
        row.addWidget(self.save)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button('使用 Windows 定位',service.locate))
        row.addWidget(button('立即刷新',service.refresh))
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(plain('仅在点击定位时请求 Windows 位置。只将约略坐标用于天气查询。'))
        self.status = plain()
        layout.addWidget(self.status)
        layout.addWidget(self.options())
        layout.addWidget(plain('每日摘要与安静时段使用电脑本地时间。Open‑Meteo 的普通预报不包含官方预警。'))
        layout.addStretch()
        service.results.connect(self.show_results)
        service.location_failed.connect(self.status.setText)
        service.changed.connect(self.reload)
        self.reload()

    def options(self):
        settings = QWidget(self)
        form = QFormLayout(settings)
        cfg = self.service.cfg
        for key,text in (('enabled','启用天气功能'),('auto','主动天气提醒'),
                         ('voice','主动提醒同时使用语音'),('depart_enabled','启用预计出门带伞提醒')):
            widget = QCheckBox(text)
            widget.setChecked(cfg[key])
            widget.toggled.connect(lambda value,k=key:self.service.set(k,value))
            form.addRow(widget)
        for key,text in (('summary','每日简报'),('quiet_start','安静时段开始'),
                         ('quiet_end','安静时段结束'),('depart','预计出门时间')):
            widget = QTimeEdit(QTime.fromString(cfg[key],'HH:mm'))
            widget.setDisplayFormat('HH:mm')
            widget.timeChanged.connect(lambda value,k=key:self.service.set(k,value.toString('HH:mm')))
            form.addRow(text,widget)
        for key,text,minimum,maximum,suffix in (
            ('interval','自动刷新间隔',30,60,' 分钟'),('rain','降雨提醒概率',30,100,'%'),
            ('hot','高温提醒阈值',20,50,' °C'),('cold','低温提醒阈值',-30,15,' °C')):
            widget = QSpinBox()
            widget.setRange(minimum,maximum)
            widget.setSuffix(suffix)
            widget.setValue(cfg[key])
            widget.valueChanged.connect(lambda value,k=key:self.service.set(k,value))
            form.addRow(text,widget)
        return settings

    def reload(self):
        location = self.service.cfg['location']
        self.place.setText('天气位置 · '+(location['name'] if location else '尚未设置'))
        if self.service.forecast_request:
            self.status.setText('正在更新预报…')
        elif self.service.error:
            self.status.setText(self.service.error)
        elif self.service.updated:
            self.status.setText('已更新 · '+datetime.fromtimestamp(self.service.updated).strftime('%H:%M'))

    def show_results(self, results, notice):
        self.choices.clear()
        for item in results:
            self.choices.addItem(item['name'],item)
        self.save.setEnabled(bool(results))
        self.status.setText(notice or '请选择后保存。')

    def save_city(self):
        location = self.choices.currentData()
        if location:
            self.service.select(location)


def text_label(text='', name='', *, center=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    if center:
        widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return widget


def hint(data, cfg):
    messages = list(insight(data, cfg, datetime.now()))
    storm = next((entry for entry in messages if entry[0]=='storm'), None)
    selected = storm or (messages[0] if messages else None)
    return selected[1:] if selected else None


def temperature(row, key, unit='°'):
    value = number(row, key)
    return '—' if value is None else f'{value:.1f}{unit}'


def action_icon(kind, ink, dpr):
    pixmap = QPixmap(round(14*dpr),round(14*dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(ink),1.15,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        path = QPainterPath()
        if kind=='speak':
            path.moveTo(2,5)
            for point in ((4.1,5),(7.4,2.5),(7.4,11.5),(4.1,9),(2,9)):
                path.lineTo(*point)
            path.closeSubpath()
            painter.drawArc(QRectF(5,3.5,6.5,7),-58*16,116*16)
        elif kind=='refresh':
            painter.drawArc(QRectF(2.4,2.5,9.2,9.2),40*16,290*16)
            path.moveTo(9,2)
            path.lineTo(11.4,4.2)
            path.lineTo(12.4,1.7)
        else:
            for index in range(24):
                angle = index*pi/12
                radius = 5.25 if index%4 in (1,2) else 4.1
                point = QPointF(7+radius*cos(angle),7+radius*sin(angle))
                path.lineTo(point) if index else path.moveTo(point)
            path.closeSubpath()
            painter.drawEllipse(QPointF(7,7),1.65,1.65)
        painter.drawPath(path)
    return QIcon(pixmap)


class HoursScroll(QScrollArea):
    def wheelEvent(self, event):
        horizontal = event.pixelDelta().x() or event.angleDelta().x()//2
        if not horizontal and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            horizontal = event.pixelDelta().y() or event.angleDelta().y()//2
        if horizontal:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value()-horizontal)
            event.accept()
        else:
            event.ignore()


class WeatherBubble(QWidget):
    def __init__(self, pet, service, settings, speak):
        super().__init__(pet,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle('TT 天气预报')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.pet,self.service = pet,service
        self.speak = speak
        self.playback = pet.chat.pro_voice
        self.speech_files = ()
        self.speech_notice = ''
        self.rendered = None
        self.hour_cards = []
        self.tail = QPointF()
        self.theme = None
        self.contents = QVBoxLayout(self)
        self.contents.setContentsMargins(44,33,44,35)
        self.contents.setSpacing(7)
        row = QHBoxLayout()
        self.city = plain('天气')
        self.city.setObjectName('weatherCity')
        self.city.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.city,1)
        close = button('×',self.hide)
        close.setAccessibleName('关闭天气预报')
        close.setFixedWidth(28)
        row.addWidget(close)
        self.contents.addLayout(row)
        self.current_section = QWidget()
        section = QVBoxLayout(self.current_section)
        section.setContentsMargins(0,0,0,0)
        section.setSpacing(3)
        hero = QHBoxLayout()
        hero.setSpacing(8)
        self.weather_icon = text_label(name='weatherIcon', center=True)
        self.weather_icon.setFixedWidth(38)
        hero.addWidget(self.weather_icon)
        self.current = text_label(name='weatherCurrent')
        hero.addWidget(self.current)
        self.condition = plain()
        self.condition.setObjectName('weatherCondition')
        hero.addWidget(self.condition,1)
        section.addLayout(hero)
        measures = QHBoxLayout()
        measures.setSpacing(4)
        self.metrics = []
        for icon, caption in (('♨','体感'),('◉','湿度'),('≋','风速'),('☂','降雨概率')):
            card = QWidget()
            card.setObjectName('weatherMetric')
            layout = QVBoxLayout(card)
            layout.setContentsMargins(3,3,3,3)
            layout.setSpacing(0)
            layout.addWidget(text_label(f'{icon} {caption}','weatherMeasure',center=True))
            value = text_label(name='weatherValue',center=True)
            value.setAccessibleName(caption)
            layout.addWidget(value)
            measures.addWidget(card,1)
            self.metrics.append(value)
        section.addLayout(measures)
        self.contents.addWidget(self.current_section)
        self.reminder = QWidget()
        self.reminder.setObjectName('weatherReminder')
        reminder = QVBoxLayout(self.reminder)
        reminder.setContentsMargins(8,3,8,3)
        reminder.setSpacing(1)
        self.reminder_title = text_label(name='weatherReminderTitle')
        self.reminder_text = plain()
        reminder.addWidget(self.reminder_title)
        reminder.addWidget(self.reminder_text)
        self.contents.addWidget(self.reminder)
        self.empty = plain()
        self.contents.addWidget(self.empty)
        self.scroller = QScrollArea()
        self.scroller.setObjectName('weatherForecastScroll')
        self.scroller.setWidgetResizable(True)
        self.scroller.setMinimumHeight(22)
        self.scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.forecast_panel = QWidget()
        forecast = QVBoxLayout(self.forecast_panel)
        forecast.setContentsMargins(0,0,4,0)
        forecast.setSpacing(3)
        forecast.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        forecast.addWidget(text_label('未来三天','weatherSection'))
        self.days = QWidget()
        self.days.setObjectName('weatherDays')
        self.daily_grid = QGridLayout(self.days)
        self.daily_grid.setContentsMargins(6,3,6,3)
        self.daily_grid.setHorizontalSpacing(5)
        self.daily_grid.setVerticalSpacing(1)
        self.daily_grid.setColumnStretch(2,1)
        forecast.addWidget(self.days)
        hourly = QHBoxLayout()
        hourly.setSpacing(4)
        hourly.addWidget(text_label('逐小时 · 当地时间','weatherSection'),1)
        self.hour_left = button('‹',lambda:self.step_hours(-1))
        self.hour_right = button('›',lambda:self.step_hours(1))
        for control, name in ((self.hour_left,'向前查看逐小时预报'),(self.hour_right,'向后查看逐小时预报')):
            control.setFixedSize(23,20)
            control.setObjectName('weatherPager')
            control.setAccessibleName(name)
            control.setToolTip(name)
            hourly.addWidget(control)
        forecast.addLayout(hourly)
        self.hours = HoursScroll()
        self.hours.setObjectName('weatherHoursScroll')
        self.hours.setFixedHeight(70)
        self.hours.setWidgetResizable(True)
        self.hours.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        strip = QWidget()
        self.hourly_layout = QHBoxLayout(strip)
        self.hourly_layout.setContentsMargins(0,0,0,1)
        self.hourly_layout.setSpacing(4)
        self.hours.setWidget(strip)
        hour_slider = self.hours.horizontalScrollBar()
        hour_slider.valueChanged.connect(self.update_hour_controls)
        hour_slider.rangeChanged.connect(self.update_hour_controls)
        forecast.addWidget(self.hours)
        forecast.addStretch()
        self.scroller.setWidget(self.forecast_panel)
        self.contents.addWidget(self.scroller,1)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        self.source = QLabel('<a href="https://open-meteo.com/">Open‑Meteo</a>')
        self.source.setObjectName('weatherSource')
        self.source.setOpenExternalLinks(True)
        meta.addWidget(self.source)
        meta.addStretch()
        self.notice = plain()
        self.notice.setObjectName('weatherMeta')
        self.notice.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        meta.addWidget(self.notice)
        self.contents.addLayout(meta)
        self.error_text = plain()
        self.error_text.setObjectName('weatherMeta')
        self.contents.addWidget(self.error_text)
        row = QHBoxLayout()
        row.setSpacing(5)
        self.voice = button('播报天气',self.toggle_speech)
        self.voice.setObjectName('weatherSpeak')
        row.addWidget(self.voice)
        self.refresh_button = button('刷新',service.refresh)
        self.settings_button = button('天气设置',settings)
        self.refresh_button.setObjectName('weatherAction')
        self.settings_button.setObjectName('weatherAction')
        for control in (self.voice,self.refresh_button,self.settings_button):
            control.setFixedHeight(28)
            control.setIconSize(QSize(14,14))
            control.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        row.addWidget(self.refresh_button)
        row.addWidget(self.settings_button)
        self.contents.addLayout(row)
        self.follow = QTimer(self,interval=120)
        if not hasattr(pet,'window_manager'):
            self.follow.timeout.connect(self.place)
        self.follow.timeout.connect(self.update_actions)
        self.resize(404,449)
        service.changed.connect(self.reload)
        if not hasattr(pet,'window_manager'):
            pet.installEventFilter(self)
        self.reload()

    def update_theme(self):
        theme = palette_for(self.pet)
        if self.theme is theme:
            return
        self.theme = theme
        for kind, control in (('speak',self.voice),('refresh',self.refresh_button),('settings',self.settings_button)):
            control.setIcon(action_icon(kind,theme.ink,self.devicePixelRatioF()))
        self.update_source(self.owns_speech())
        color = '255,255,255' if theme.dark else '60,30,80'
        accent = '195,143,234'
        self.setStyleSheet(f'QWidget {{color:{theme.ink};background:transparent}} '
                           f'QLabel#weatherCity {{font-size:16px;font-weight:650}} '
                           f'QLabel#weatherCurrent {{font-size:31px;font-weight:750}} '
                           f'QLabel#weatherIcon {{font-size:27px}} '
                           f'QLabel#weatherCondition {{font-size:14px;font-weight:600}} '
                           f'QLabel#weatherMeasure, QLabel#weatherMeta, QLabel#weatherSource '
                           f'{{font-size:11px;color:{theme.secondary}}} '
                           f'QLabel#weatherValue, QLabel#weatherReminderTitle, QLabel#weatherSection '
                           '{font-weight:700} '
                           f'QWidget#weatherMetric, QWidget#weatherDays, QWidget#weatherHour '
                           f'{{background:rgba({color},23);border-radius:7px}} '
                           f'QWidget#weatherReminder {{background:rgba({accent},53);border-radius:8px}} '
                           f'QPushButton {{border-radius:7px;padding:5px 9px;background:rgba({color},33)}} '
                           f'QPushButton:hover {{background:rgba({color},60)}} '
                           f'QPushButton:disabled {{color:{theme.secondary};background:transparent}} '
                           'QPushButton#weatherSpeak, QPushButton#weatherAction {padding:3px 4px} '
                           f'QPushButton#weatherSpeak:enabled {{background:rgba({accent},84);font-weight:700}} '
                           f'QPushButton#weatherSpeak:hover {{background:rgba({accent},110)}} '
                           f'QPushButton#weatherPager {{padding:0;font-size:15px}} '
                           'QScrollArea {border:0} '
                           'QScrollBar:vertical {width:5px;margin:0;background:transparent} '
                           'QScrollBar:horizontal {height:5px;margin:0;background:transparent} '
                           f'QScrollBar::handle {{background:rgba({accent},112);border-radius:2px;'
                           'min-height:19px;min-width:19px} '
                           f'QScrollBar::handle:hover {{background:rgba({accent},205)}} '
                           'QScrollBar::add-line,QScrollBar::sub-line {height:0;width:0} '
                           'QScrollBar::add-page,QScrollBar::sub-page {background:transparent}')
        self.update()

    def reload(self):
        service = self.service
        location = service.cfg['location']
        self.city.setText(location['name'] if location else '天气')
        data = service.data if service.cfg['enabled'] else None
        stale = bool(data and (service.error or time.time()-service.updated>service.cfg['interval']*60+180))
        self.fresh = bool(data and not stale)
        self.current_section.setVisible(bool(data))
        self.scroller.setVisible(bool(data))
        self.empty.setVisible(not data)
        self.empty.setText('天气功能已关闭。' if not service.cfg['enabled'] else
                           '请在天气设置中选择城市。' if not location else '暂无预报。')
        if data and data is not self.rendered:
            self.rendered = data
            self.render(data)
        reminder = hint(data,service.cfg) if self.fresh else None
        self.reminder.setVisible(bool(reminder))
        if reminder:
            icon, text = reminder
            self.reminder_title.setText(f'{icon}  TT 的天气提醒')
            self.reminder_text.setText(text)
        if data:
            clock = datetime.fromtimestamp(service.updated).strftime('%m/%d %H:%M')
            status = f'{"缓存" if stale else "更新"} {clock}'
        else:
            status = ''
        self.notice.setText(status)
        self.notice.setVisible(bool(status))
        if service.forecast_request:
            self.speech_notice = ''
        self.update_actions()

    def render(self,data):
        current,hours,days = forecasts(data)
        icon, name = label(number(current,'weather_code'))
        self.weather_icon.setText(icon)
        self.current.setText(temperature(current,'temperature_2m','°C'))
        self.condition.setText(name)
        for widget, value in zip(self.metrics,(
                temperature(current,'apparent_temperature','°C'),
                format_value(number(current,'relative_humidity_2m'),'%'),
                format_value(number(current,'wind_speed_10m'),' km/h'),
                format_value(number(hours[0],'precipitation_probability'),'%') if hours else '—')):
            widget.setText(value)
        for layout in (self.daily_grid,self.hourly_layout):
            while layout.count():
                widget = layout.takeAt(0).widget()
                widget.hide()
                widget.deleteLater()
        for i, row in enumerate(days):
            kind, description = label(number(row,'weather_code'))
            temperatures = temperature(row,'temperature_2m_min')+'～'+temperature(row,'temperature_2m_max')
            probability = format_value(number(row,'precipitation_probability_max'),'%')
            for col, content in enumerate((row['time'][5:10].replace('-','/'),f'{kind} {description}',
                                           temperatures,f'雨 {probability}')):
                cell = text_label(content)
                if col==2:
                    cell.setAlignment(Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignVCenter)
                elif col==3:
                    cell.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
                    cell.setToolTip(f'降雨概率 {probability}')
                self.daily_grid.addWidget(cell,i,col)
        self.hour_cards.clear()
        for row in hours:
            kind, description = label(number(row,'weather_code'))
            card = QWidget()
            card.setObjectName('weatherHour')
            card.setFixedWidth(58)
            card.setToolTip(description)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(2,1,2,1)
            layout.setSpacing(0)
            for content, style in ((row['time'][11:16],'weatherMeasure'),(kind,''),
                    (temperature(row,'temperature_2m'),'weatherValue'),
                    (format_value(number(row,'precipitation_probability'),'%'),'weatherMeasure')):
                layout.addWidget(text_label(content,style,center=True))
            self.hourly_layout.addWidget(card)
            self.hour_cards.append(card)
        self.hours.horizontalScrollBar().setValue(0)
        self.update_hour_controls()

    def step_hours(self, direction):
        bar = self.hours.horizontalScrollBar()
        step = max(62,bar.pageStep()-62)
        bar.setValue(bar.value()+direction*step)

    def update_hour_controls(self, *_):
        bar = self.hours.horizontalScrollBar()
        self.hour_left.setEnabled(bar.value()>bar.minimum())
        self.hour_right.setEnabled(bar.value()<bar.maximum())

    def owns_speech(self):
        return bool(self.speech_files and self.playback.player.has_audio(self.speech_files))

    def toggle_speech(self):
        if self.owns_speech():
            self.speech_files = ()
            self.playback.stop()
            self.speech_notice = ''
        elif self.fresh:
            self.speech_files,self.speech_notice = self.speak()
        self.update_actions()

    def update_source(self, speaking):
        if self.theme is None:
            return
        suffix = '模型预报'
        if speaking:
            suffix = 'TT 正在播报' if self.playback.status=='播放中' else 'TT 本地语音生成中'
        value = f'<a style="color:{self.theme.ink}" href="https://open-meteo.com/">Open‑Meteo</a> · {suffix}'
        if self.source.text()!=value:
            self.source.setText(value)

    def update_actions(self):
        service = self.service
        owns = self.owns_speech()
        if self.speech_files and not owns:
            if self.playback.status.startswith('失败'):
                self.speech_notice = '播报'+self.playback.status
            self.speech_files = ()
        busy = self.playback.active or self.pet.chat.busy()
        self.voice.setText('停止播报' if owns else '播报天气')
        self.voice.setToolTip(
            '再次点击停止。首次启动本地语音可能需要等待。' if owns else
            '需要有效的天气数据，请先刷新预报。' if not self.fresh else
            'TT 正在回复或朗读，请稍后再播报。' if busy else
            '使用 TT 语音设置中选中的本地音色，朗读天气和提醒。')
        self.voice.setEnabled(owns or (self.fresh and not busy))
        self.refresh_button.setText('更新中…' if service.forecast_request else '刷新')
        self.refresh_button.setEnabled(bool(service.cfg['enabled'] and service.cfg['location']
                                           and not service.forecast_request))
        self.update_source(owns)
        message = service.error or self.speech_notice
        self.error_text.setText(message)
        self.error_text.setVisible(bool(message))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        width, height = event.size().width(), event.size().height()
        side = 44 if width>=370 else 36 if width>=320 else 32
        top, bottom = (33,35) if height>=420 else (29,29)
        self.contents.setContentsMargins(side,top,side,bottom)
        self.contents.setSpacing(4 if height>=420 else 3)

    def place(self):
        self.update_theme()
        screen = self.pet.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.resize(min(404,area.width()),min(449,area.height()))
        anchor = QRectF(self.pet.bubble_anchor_rect()).translated(self.pet.x(),self.pet.y())
        position,tail = bubble_placement(area,anchor,self.width(),self.height())
        if position != self.pos():
            self.move(position)
        if self.tail != tail:
            self.tail = tail
            self.update()

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(20,16,self.width()-40,self.height()-32)
        if self.theme:
            paint_glass(painter,rect,self.theme,tail_direction(outline(rect),rect,self.tail))
            paint_motif(painter,QPointF(rect.right()-52,rect.top()+2),23,self.theme)

    def showEvent(self,event):
        super().showEvent(event)
        self.reload()
        self.place()
        self.follow.start()

    def hideEvent(self,event):
        self.follow.stop()
        super().hideEvent(event)

    def eventFilter(self,watched,event):
        if watched is self.pet and event.type()==QEvent.Type.Hide:
            self.hide()
        return super().eventFilter(watched,event)
