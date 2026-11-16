import sys
from tt_single_instance import create_application
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


def main():
    app = create_application(sys.argv)
    if app is None:
        return 0
    window = QWidget()
    window.setWindowTitle("个人 AI 桌宠 · 基础检查")
    window.resize(460, 200)

    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("模块化程序入口已启动"))
    layout.addWidget(QLabel("当前仅运行本地界面，尚未接入 AI。"))
    layout.addWidget(QLabel("关闭窗口即可退出。"))

    window.show()
    result = app.exec()
    print("Window closed normally")
    return result


if __name__ == "__main__":
    sys.exit(main())
