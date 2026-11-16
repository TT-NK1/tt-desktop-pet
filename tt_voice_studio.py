"""Local sample inspection UI. Model-dependent controls remain explicitly unavailable."""
import json

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QLabel,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from tt_voice_analysis import analyze


class AnalysisWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, path, parent):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.completed.emit(analyze(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))


class VoiceStudio(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('授权声音工作室（本地分析预览）')
        self.resize(620, 620)
        self.path = None
        self.worker = None
        layout = QVBoxLayout(self)
        note = QLabel('样本只在本地读取，原文件不变。当前支持 PCM WAV，其他格式需要额外解码器。\n'
                      '参考音频零样本合成不等于训练专属模型，不保证与原声一致。\n'
                      '禁止用于欺诈冒充或绕过身份验证。本页未接入云服务，不上传音频或文字。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.consent = QCheckBox('声音属于本人或已获得明确授权，且本次用途在授权范围内')
        layout.addWidget(self.consent)
        self.import_button = QPushButton('导入样本')
        self.import_button.clicked.connect(self.import_sample)
        layout.addWidget(self.import_button)
        self.analyze_button = QPushButton('分析录音 / 重试')
        self.analyze_button.clicked.connect(self.start_analysis)
        layout.addWidget(self.analyze_button)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText('输入要朗读的文字（尚未接入参考音频合成模型）')
        layout.addWidget(self.text)
        for label in ('生成 AI 合成语音', '试听 AI 合成结果', '导出 AI 合成音频及说明'):
            button = QPushButton(label + '（未配置模型）')
            button.setEnabled(False)
            layout.addWidget(button)
        delete = QPushButton('移除样本引用并清空本页数据（保留原文件）')
        delete.clicked.connect(self.clear_data)
        layout.addWidget(delete)
        self.status = QLabel('未创建样本副本、缓存或合成文件。转写、模型 VAD、语速、音高及多人检测待接入。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def import_sample(self):
        if not self.consent.isChecked():
            self.status.setText('请先确认声音授权。')
            return
        path, _ = QFileDialog.getOpenFileName(self, '选择授权录音', '',
                                             '音频 (*.wav *.mp3 *.m4a *.flac);;所有文件 (*)')
        if path:
            self.path = path
            self.report.clear()
            self.status.setText('已选择：' + path + '；点击分析验证能否解码。')

    def start_analysis(self):
        if not self.path or not self.consent.isChecked():
            self.status.setText('请确认授权并导入样本。')
            return
        if self.worker and self.worker.isRunning():
            return
        self.report.clear()
        self.status.setText('正在读取并分析本地音频……')
        self.import_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
        self.worker = AnalysisWorker(self.path, self)
        self.worker.completed.connect(self.show_report)
        self.worker.failed.connect(lambda error: self.status.setText('分析失败：' + error + ' 可重新导入后重试。'))
        self.worker.finished.connect(self.analysis_finished)
        self.worker.start()

    def show_report(self, report):
        self.report.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.status.setText('基础分析完成；请阅读报告中的局限。')

    def analysis_finished(self):
        self.import_button.setEnabled(True)
        self.analyze_button.setEnabled(True)

    def clear_data(self):
        if self.worker and self.worker.isRunning():
            self.status.setText('分析进行中，请完成后再清空。')
            return
        self.path = None
        self.report.clear()
        self.text.clear()
        self.consent.setChecked(False)
        self.status.setText('本页数据已清空；原文件保留。未创建缓存或合成文件。')

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.status.setText('分析进行中，完成后可关闭。')
            return
        self.clear_data()
        super().reject()
