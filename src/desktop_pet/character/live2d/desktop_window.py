"""Transparent desktop host; unavailable art is never substituted with another outfit."""
import json
import math
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget

from .outfit_manager import OutfitManager
from .state_machine import State, StateMachine
from .motion_controller import MotionController
from .autonomous_idle import AutonomousIdleManager
from .web_renderer import WebRenderer


EXPRESSION_NAMES = [('normal','普通'),('smile','微笑'),('shy','害羞'),('blush','脸红'),
                    ('sleepy','困倦'),('puzzled','疑惑'),('annoyed','轻微不满'),('surprised','惊讶')]


def clamp_position(x, y, width, height, screens):
    """Keep the entire pet on a real monitor, including negative-coordinate monitors."""
    center = QPoint(x + width//2, y + height//2)
    target = next((s for s in screens if s.contains(center)), None)
    if target is None:
        target = min(screens, key=lambda s:(s.center().x()-center.x())**2+(s.center().y()-center.y())**2)
    return QPoint(max(target.left(), min(x, target.right()-width+1)),
                  max(target.top(), min(y, target.bottom()-height+1)))


class DesktopWindow(QWidget):
    size_presets = (.75, 1., 1.25, 1.5)
    window_type = Qt.WindowType.Window
    separate_tray_menu = False

    def __init__(self, root, verify=False, *, settings_database=None):
        super().__init__(None,self.window_type | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.root = Path(root)
        self.manager = OutfitManager(root, database=":memory:" if verify else settings_database)
        self.states = StateMachine()
        self.motions = MotionController(self.states, {})
        self.autonomous = AutonomousIdleManager(self.states)
        self.autonomous.enabled = self.manager.get_setting('automatic', True)
        self.auto_playing = None
        self.renderer = WebRenderer(root, self, verify=verify)
        self.renderer.ready.connect(self.model_ready)
        self.renderer.failed.connect(self.load_failed)
        self.renderer.motion_finished.connect(self.motion_finished)
        self.renderer.motion_failed.connect(self.motion_failed)
        self.renderer.closed.connect(self.cleaned)
        self.setWindowTitle('少女桌宠 · Live2D')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.renderer.widget)
        self.resize(330,390)
        self.resize_pet(float(self.manager.get_setting('scale',1)))
        pos = self.manager.get_setting('position', None)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move_clamped(*(pos if isinstance(pos,list) and len(pos)==2 else (screen.right()-self.width()-24,screen.bottom()-self.height()-24)))
        self.pending_outfit = self.manager.selected
        self.menu_outfit = self.manager.selected
        self.loaded = False
        self.queued_outfit = None
        self.drag_start = None
        self.drag_offset = QPoint()
        self.dragging = False
        self.last_cursor = QCursor.pos()
        self.last_tick = time.monotonic()
        self.last_click = -10
        self.blink_until = 0
        self.mouth_open = 0.0
        self.gaze_x = self.gaze_y = self.inertia = 0.
        self.current_motion = None
        self.continuous_motion = None
        self.closing = False
        self.cleanup_ack = False
        self.verify = verify
        self.report = {'status':'running','checks':{},'samples':[]}
        self.started = time.monotonic()
        self.ready_at = None
        self.test_stage = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.action_dispatch = QTimer(self, singleShot=True, interval=0)
        self.action_dispatch.timeout.connect(self.dispatch_action)
        self.timer.start(33)
        QApplication.instance().installEventFilter(self)
        pix = QPixmap(32,32);pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix);painter.setBrush(QColor('#705479'));painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2,2,28,28);painter.end()
        self.tray = QSystemTrayIcon(QIcon(pix), self)
        self.tray.setToolTip('少女桌宠 · 右键管理')
        self.menu = QMenu()
        self.build_menu()
        self.tray_menu = self.menu
        if self.separate_tray_menu:
            self.tray_menu = QMenu(self)
            self.tray_menu.addAction('退出', self.close)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()
        try:
            path = self.manager.model_path(self.pending_outfit)
        except ValueError:
            self.pending_outfit = 'street'
            path = self.manager.model_path('street')
        self.load_outfit_model(path, self.pending_outfit)

    def resize_pet(self, scale):
        self.scale = self.normalize_scale(scale)
        self.resize(round(330*self.scale),round(390*self.scale))
        if hasattr(self,'manager'):self.manager.save_setting('scale',self.scale)

    def normalize_scale(self, scale):
        return max(.6, min(1.8, scale))

    def move_clamped(self,x,y):
        self.move(clamp_position(int(x),int(y),self.width(),self.height(),[s.availableGeometry() for s in QApplication.screens()]))

    def build_menu(self):
        self.menu.clear()
        outfits = self.menu.addMenu('服装')
        for key, entry in self.manager.config.items():
            if not entry.get('model'):
                continue
            name = entry['name']
            if key == (self.queued_outfit or self.pending_outfit) and key != self.manager.selected:
                name += '（切换中…）'
            action = outfits.addAction(name)
            action.setCheckable(True)
            action.setChecked(key == self.menu_outfit)
            action.triggered.connect(lambda checked=False,k=key:self.select_outfit(k))
        outfits.addSeparator()
        follow = outfits.addAction('换装时恢复该服装的动作')
        follow.setCheckable(True)
        follow.setChecked(self.manager.get_setting('switch_outfit_action', True))
        follow.toggled.connect(lambda enabled:self.manager.save_setting('switch_outfit_action',enabled))

        browsing = self.menu_outfit != self.manager.selected
        title = '动作' if not browsing else f'动作（{self.manager.config[self.menu_outfit]["name"]}）'
        motions = self.menu.addMenu(title)
        motions.menuAction().setEnabled(self.loaded and not browsing)
        supported = self.manager.config[self.menu_outfit].get('motions',{})
        for key,name in self.manager.action_labels(self.menu_outfit).items():
            if key != 'idle' and key not in supported:
                continue
            action = motions.addAction(name)
            action.triggered.connect(lambda checked=False,k=key:self.select_motion(k))
        expressions=self.menu.addMenu('表情')
        outfit = self.manager.config[self.manager.selected]
        available = outfit.get('expressions', {})
        labels = outfit.get('expression_labels')
        for key, name in (labels.items() if labels else EXPRESSION_NAMES):
            if key not in available:
                continue
            action = expressions.addAction(name)
            action.triggered.connect(lambda checked=False,k=key:self.select_expression(k))
        auto=self.menu.addAction('自主日常动作');auto.setCheckable(True)
        auto.setChecked(self.autonomous.enabled)
        follow.setEnabled(not self.autonomous.enabled)
        follow.setToolTip('开启自主日常动作时，换装后从自然待机开始。')
        auto.toggled.connect(self.set_automatic)
        size=self.menu.addMenu('大小')
        for value in self.size_presets:
            size.addAction(f'{round(value*100)}%', lambda value=value:self.change_size(value))
        self.menu.addSeparator()
        self.menu.addAction('退出', self.close)

    def change_size(self,value):
        old=self.geometry().bottomRight();self.resize_pet(value)
        self.move_clamped(old.x()-self.width(),old.y()-self.height())

    def model_ready(self,state):
        switching = self.loaded and self.pending_outfit != self.manager.selected
        previous = self.states.requests.get('user')
        if switching:
            self.motions.select('idle', time.monotonic())
            self.autonomous.reset()
            self.auto_playing = None
            self.current_motion = None
            self.continuous_motion = None
        self.manager.commit(self.pending_outfit)
        self.menu_outfit = self.manager.selected
        self.loaded=True
        self.ready_at=time.monotonic()
        self.motions=MotionController(self.states,self.manager.config[self.manager.selected].get('motions',{}))
        self.autonomous.set_outfit(self.manager.selected, self.motions.motions)
        if switching and not self.autonomous.enabled:
            choice = self.manager.outfit_motion(self.manager.selected, previous.state.value if previous else 'idle')
            self.motions.select(choice,self.ready_at)
        expression = self.manager.get_setting('expression', 'normal')
        expressions = self.manager.config[self.manager.selected].get('expressions', {})
        name = expressions.get(expression, expressions.get('normal'))
        if name:
            self.renderer.set_expression(name)
        self.build_menu()
        self.action_dispatch.start()

    def load_failed(self,message):
        self.pending_outfit = self.manager.selected
        self.menu_outfit = self.manager.selected
        self.build_menu()
        if self.verify:
            self.report['failure']=message;self.close()
        else:QMessageBox.warning(self,'角色加载失败',message)

    def load_outfit_model(self, path, key):
        self.renderer.load_model(path, body_variant=self.manager.config[key].get('body_variant'))

    def select_outfit(self,key):
        if key not in self.manager.config:
            return
        if self.queued_outfit is None and key == self.pending_outfit and (self.loaded or self.renderer.candidate is not None):
            return
        entry = self.manager.config[key]
        if not entry.get('model'):
            self.menu_outfit = key
            self.build_menu()
            return
        self.reset_autonomous()
        if self.renderer.candidate is not None or self.renderer.retiring_view is not None:
            self.queued_outfit = key
            self.menu_outfit = key
            self.build_menu()
            return
        self.queued_outfit = None
        try:
            path = self.manager.model_path(key)
            if not self.renderer.select_variant(path, entry.get('body_variant')):
                self.load_outfit_model(path, key)
            self.pending_outfit = key
            self.menu_outfit = key
            self.build_menu()
        except (ValueError,RuntimeError) as error:
            self.load_failed(str(error))

    def set_automatic(self,enabled):
        self.manager.save_setting('automatic',bool(enabled))
        self.autonomous.enabled = enabled
        self.reset_autonomous()
        self.build_menu()

    def reset_autonomous(self):
        self.autonomous.reset()
        if self.auto_playing is not None:
            self.stop_motion()

    def update_autonomous(self, now, state):
        snapshot = self.renderer.snapshot
        outfit = self.manager.config[self.manager.selected]
        music = self.autonomous_music_playing()
        interrupted = (not self.animation_visible() or self.pending_outfit != self.manager.selected
                       or self.queued_outfit is not None or self.renderer.pending_variant is not None
                       or self.drag_start is not None or self.autonomous_voice_active()
                       or (self.autonomous.current == State.LISTENING_MUSIC and not music))
        blocked = (interrupted or QApplication.activePopupWidget() is not None
                   or QApplication.activeModalWidget() is not None)
        if interrupted and self.autonomous.current is not None:
            self.reset_autonomous()
        failed = snapshot.get('streetPoseErrors') or {}
        unavailable = {action for action, motion in self.motions.motions.items() if motion['group'] in failed}
        # Do not start counting while a prop is still being put away on the renderer.
        idle = (state == State.IDLE and self.current_motion is None and snapshot.get('ready')
                and snapshot.get('streetPoseName') == outfit.get('body_variant') and not snapshot.get('switching'))
        return self.autonomous.tick(now, idle=idle, blocked=blocked,
                                    music=music, unavailable=unavailable)

    def autonomous_voice_active(self):
        return False

    def autonomous_music_playing(self):
        return False

    def select_motion(self,name):
        now=time.monotonic()
        self.motions.select(name,now)
        self.reset_autonomous()
        if name == 'idle':
            self.states.release('auto')
            self.stop_motion()
        self.manager.save_setting('last_motion',name)
        self.manager.remember_motion(self.manager.selected,name)
        self.action_dispatch.start()

    def dispatch_action(self):
        if not self.loaded or self.closing or self.renderer.retiring_view is not None:
            return
        state = self.motions.tick(time.monotonic())
        motion = self.motions.motions.get(state.value)
        if motion and state != self.current_motion:
            self.current_motion = state
            self.continuous_motion = state if motion.get('continuous') else None
            self.renderer.play_motion(motion['group'], motion.get('index', 0))

    def stop_motion(self):
        if self.current_motion is None and self.continuous_motion is None:
            return
        self.renderer.command('sdkProbe.clearStreetPose()')
        self.auto_playing = None
        self.continuous_motion = None
        self.current_motion = None

    def motion_failed(self, motion_id, message):
        state = self.current_motion
        motion = self.motions.motions.get(state.value) if state else None
        if not motion or f"{motion['group']}_{motion.get('index', 0)}" != motion_id:
            return
        if self.auto_playing == state:
            self.autonomous.unavailable(state)
            self.stop_motion()
            return
        self.motions.finished(state)
        self.stop_motion()
        QMessageBox.warning(self,'动作加载失败',message)

    def select_expression(self,name):
        entry=self.manager.config[self.manager.selected].get('expressions',{})
        if name not in entry:return
        self.renderer.set_expression(entry[name])
        self.manager.save_setting('expression',name)

    def motion_finished(self):
        if self.current_motion:
            state = self.current_motion
            if self.auto_playing == state:
                self.autonomous.finish(state)
                self.continuous_motion = None
                self.auto_playing = None
            else:
                self.motions.finished(state)
            self.current_motion=None

    def eventFilter(self,watched,event):
        if self.closing:return False
        kind=event.type()
        mouse_kinds=(QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonRelease,QEvent.Type.MouseMove,QEvent.Type.MouseButtonDblClick)
        if kind not in mouse_kinds:return False
        if not isinstance(watched,QWidget) or not (watched is self.renderer.widget or self.renderer.widget.isAncestorOf(watched)):return False
        if kind==QEvent.Type.MouseButtonPress:
            if event.button()==Qt.MouseButton.RightButton:
                self.menu.popup(QCursor.pos());return True
            if event.button()==Qt.MouseButton.LeftButton:
                self.drag_start=event.globalPosition().toPoint();self.drag_offset=self.drag_start-self.pos();self.renderer.widget.grabMouse();return True
        if kind==QEvent.Type.MouseMove and self.drag_start is not None:
            cursor=event.globalPosition().toPoint()
            if (cursor-self.drag_start).manhattanLength()>QApplication.startDragDistance():
                self.dragging=True;self.states.request('drag',State.DRAGGING,time.monotonic())
                point=cursor-self.drag_offset;self.move_clamped(point.x(),point.y())
            return True
        if kind==QEvent.Type.MouseButtonRelease and event.button()==Qt.MouseButton.LeftButton:
            self.release_pointer();return True
        return self.drag_start is not None

    def release_pointer(self):
        if self.drag_start is None:return
        now=time.monotonic()
        if not self.dragging and now-self.last_click>.4:
            point=self.renderer.widget.mapFromGlobal(QCursor.pos())
            if point.y()<self.renderer.widget.height()*.42:
                self.last_click=now;self.blink_until=now+.24
                self.states.request('click',State.MOUSE_CLICK,now,1.0)
        self.renderer.widget.releaseMouse();self.drag_start=None;self.dragging=False;self.states.release('drag')
        origin = self.renderer.widget.mapToGlobal(QPoint())
        self.manager.save_setting('position',[origin.x(),origin.y()])

    def animation_visible(self):
        return self.isVisible()

    def interaction_rect(self):
        return self.geometry()

    def tick(self):
        if self.closing:return
        now=time.monotonic();dt=min(.1,max(.001,now-self.last_tick));self.last_tick=now
        if self.queued_outfit is not None and self.renderer.candidate is None and self.renderer.retiring_view is None:
            key, self.queued_outfit = self.queued_outfit, None
            self.select_outfit(key)
        if not self.loaded:
            if self.verify and now-self.started>50:self.report['failure']='load timeout';self.close()
            return
        cursor=QCursor.pos();velocity=(cursor.x()-self.last_cursor.x())/dt;self.last_cursor=cursor
        if self.drag_start is not None:
            if (cursor-self.drag_start).manhattanLength()>QApplication.startDragDistance():
                self.dragging=True;self.states.request('drag',State.DRAGGING,now)
                point=cursor-self.drag_offset;self.move_clamped(point.x(),point.y())
        state=self.motions.tick(now)
        choice = self.update_autonomous(now, state)
        if choice is not None:
            state, lead = choice
            motion = self.motions.motions[state.value]
            self.current_motion = self.auto_playing = state
            self.continuous_motion = state if motion.get('continuous') else None
            self.renderer.play_motion(motion['group'], motion.get('index', 0), auto_lead=lead)
        else:
            state = self.states.current(now)
        motion = self.motions.motions.get(state.value)
        if state == State.IDLE and (self.current_motion is not None or self.continuous_motion is not None):
            self.stop_motion()
        transient = state != State.IDLE and not (motion and motion.get('continuous'))
        near = self.interaction_rect().adjusted(-150,-150,150,150).contains(cursor)
        active = self.animation_visible() and (self.dragging or transient or near)
        self.renderer.set_frame_rate(0 if not self.animation_visible() else 60 if active else 24)
        target=max(-7,min(7,-velocity*.006)) if self.dragging else 0.
        self.inertia+=(target-self.inertia)*(1-math.exp(-dt*8))
        values={}
        if self.dragging or abs(self.inertia)>.03:
            values['ParamBodyAngleZ']=self.inertia
            values['ParamHairFront']=max(-1,min(1,self.inertia*.1))
        canvas=self.renderer.widget
        point=canvas.mapFromGlobal(cursor)
        gx=max(-.65,min(.65,(point.x()/canvas.width()-.5)*1.3)) if near else 0.
        gy=max(-.65,min(.65,(.28-point.y()/canvas.height())*1.5)) if near else 0.
        weight=1-math.exp(-dt*6);self.gaze_x+=(gx-self.gaze_x)*weight;self.gaze_y+=(gy-self.gaze_y)*weight
        if state!=State.LOOK_AROUND:
            values.update(ParamEyeBallX=self.gaze_x,ParamEyeBallY=self.gaze_y)
            if self.manager.config[self.manager.selected].get('head_follow'):
                values.update(ParamAngleX=15 * self.gaze_x, ParamAngleY=9 * self.gaze_y)
        if now<self.blink_until:
            openness=abs((self.blink_until-now)/.12-1)
            values.update(ParamEyeLOpen=openness,ParamEyeROpen=openness)
        elapsed = now - self.last_click
        mouth = 0.72 * math.sin(math.pi * elapsed / 0.53) if 0 < elapsed < 0.53 else 0.0
        self.mouth_open += (mouth - self.mouth_open) * (1 - math.exp(-dt * 17))
        native_mouth = self.manager.config[self.manager.selected].get('native_mouth', False)
        if not native_mouth or self.mouth_open > .005:
            values['ParamMouthOpenY'] = self.mouth_open
        if self.animation_visible():self.send_model_parameters(values, now)
        self.dispatch_action()
        if self.verify:self.verify_tick(now)

    def send_model_parameters(self, values, now):
        self.renderer.set_parameters(values)

    def verify_tick(self,now):
        elapsed=now-self.ready_at
        if int(elapsed)>len(self.report['samples']):self.report['samples'].append(dict(self.renderer.snapshot))
        if self.test_stage==0 and elapsed>2:
            expected, _ = self.manager.prepare(self.manager.selected)
            model_name = expected.name.removesuffix('.model3.json')
            snapshot = self.renderer.snapshot
            self.report['checks']['real_model'] = (
                snapshot.get('ready') and snapshot.get('drawableCount', 0) > 0
                and snapshot.get('model', '').rstrip('/').endswith('/' + model_name)
            )
            self.report['checks']['transparent_window']=self.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.report['checks']['outfit_registry']=len(self.manager.config)==5 and all(len(self.manager.action_labels(key))==4 for key in self.manager.config)
            self.select_motion('look_around');self.test_stage=1
        if self.test_stage==1 and elapsed>11:
            self.report['checks']['motion_callback']=self.renderer.snapshot.get('motionFinished',0)>0
            self.report['checks']['returned_idle']=self.states.current(now)==State.IDLE
            self.renderer.view.grab().save(str(self.renderer.runtime/'render.png'))
            self.hide();self.test_stage=2
        if self.test_stage==2 and elapsed>13:
            self.hidden_frames=self.renderer.snapshot.get('frames');self.test_stage=3
        if self.test_stage==3 and elapsed>15:
            self.report['checks']['hidden_paused']=self.renderer.snapshot.get('frames')==self.hidden_frames
            self.show();self.change_size(.75);self.test_stage=4
        if self.test_stage==4 and elapsed>18:
            s=self.renderer.snapshot
            self.report['checks']['resumed']=s.get('frames',0)>self.hidden_frames
            self.report['checks']['webgl']=not s.get('contextLost') and not s.get('errors') and s.get('glError')==0
            self.close()

    def closeEvent(self,event):
        if self.cleanup_ack:event.accept();return
        event.ignore()
        if self.closing:return
        self.autonomous.reset()
        self.closing=True;self.timer.stop();self.tray.hide()
        QApplication.instance().removeEventFilter(self)
        self.renderer.close()

    def cleaned(self,ack):
        self.cleanup_ack=True
        self.report['checks']['cleanup']=bool(ack)
        self.report['status']='passed' if all(self.report['checks'].values()) and not self.report.get('failure') else 'failed'
        self.report['scope']='Desktop integration only; incomplete art is not accepted as complete.'
        (self.renderer.runtime/('report.json' if self.verify else 'last-session.json')).write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding='utf-8')
        self.manager.close();self.close();QApplication.instance().quit()







