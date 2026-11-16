STYLE = """
QWidget#conversation { background: #f9f5fc; color: #392c45; }
QTabWidget::pane { border: 1px solid #e2d8eb; border-radius: 11px; }
QTabBar::tab { padding: 6px 15px; color: #685675; background: #eee6f5;
    border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 3px; }
QTabBar::tab:selected { color: #563b6c; background: #f9f5fc; }
QWidget#ttCard { background: #f1e8f8; border: 1px solid #e3d5ed; border-radius: 13px; }
QLabel#cozyHeader { color: #563b6c; font-size: 22px; font-weight: 650; padding: 0; }
QLabel#characterState { color: #745388; font-size: 12px; }
QLabel#chatStatus, QLabel#chatModel, QLabel#messageSender { color: #6f5d7b; font-size: 12px; }
QLabel#chatStatus { padding: 1px 3px; }
QWidget#emptyChat, QWidget#messagePage { background: #fcfafe; border-radius: 10px; }
QLabel#emptyTitle { color: #5d4670; font-size: 19px; font-weight: 600; padding-bottom: 5px; }
QPushButton#chatSuggestion { background: #f9f4fd; border: 1px solid #e3d7ed;
    min-width: 190px; border-radius: 8px; }
QPushButton#chatSuggestion:hover { background: #eee1f7; border-color: #bda4d0; }
QScrollArea#chatHistory { border: none; background: #fcfafe; }
QLabel#messageDate { color: #6f577f; background: #f0e7f6; border-radius: 8px;
    padding: 3px 9px; font-size: 11px; }
QFrame#ttMessage { background: #ffffff; border: 1px solid #e9dfef; border-radius: 11px; }
QFrame#userMessage { background: #eee2f8; border: 1px solid #e5d5f2; border-radius: 11px; }
QTextBrowser#messageText { background: transparent; color: #362942; border: none; padding: 0;
    selection-background-color: #795591; selection-color: #ffffff; }
QPushButton#chatTool, QWidget#messageActions QPushButton { background: #f6effa; color: #584465;
    border: 1px solid #e6dbef; border-radius: 7px; padding: 4px 9px; }
QWidget#messageActions QPushButton { background: transparent; color: #796588; border-color: transparent;
    padding: 3px 7px; font-size: 11px; }
QWidget#messageActions[active="true"] QPushButton, QWidget#messageActions QPushButton:focus {
    background: #f3e9fa; color: #574469; border-color: #e5d7ef; }
QWidget#messageActions QPushButton:checked { background: #e6d8f0; color: #513465; }
QPushButton#chatTool:hover, QWidget#messageActions QPushButton:hover { background: #e9dcf3; }
QPushButton#chatTool:disabled { color: #82758c; background: #f4eef7; }
QPushButton:focus { border-color: #9670b1; }
QFrame#composer { background: #fff; border: 1px solid #dfd0ea; border-radius: 12px; }
QPlainTextEdit#chatInput { background: #fff; border: none; padding: 3px; color: #352940; }
QFrame#attachmentRow { background: #f6eef9; border-radius: 6px; }
QPushButton#cozySend { color: #fff; background: #76528e; border: 1px solid #76528e;
    border-radius: 8px; padding: 5px 12px; }
QPushButton#cozySend:hover { background: #66437e; }
QPushButton#cozySend:disabled { color: #756684; background: #e9e0ef; border-color: #e9e0ef; }
QToolButton#themeButton { background: #eee6f5; border: 1px solid #ddcee9; border-radius: 7px; }
QToolButton#themeButton:hover { background: #e9dcf3; }
QToolButton#themeButton::menu-indicator { image: none; width: 0; }
"""

DARK_STYLE = """
QWidget#conversation, QWidget#emptyChat, QWidget#messagePage, QScrollArea#chatHistory {
    background-color: $surface; color: $text;
}
QTabWidget::pane { border-color: $border; }
QTabBar::tab { background-color: $card; color: $muted; }
QTabBar::tab:selected { background-color: $surface; color: $text; }
QWidget#ttCard, QFrame#attachmentRow { background-color: $card; border-color: $border; }
QLabel#cozyHeader, QLabel#emptyTitle { color: $text; }
QLabel#chatStatus, QLabel#chatModel, QLabel#messageSender { color: $muted; }
QLabel#messageDate { color: $muted; background-color: $card; }
QFrame#ttMessage { background-color: $assistant; border-color: $border; }
QFrame#userMessage { background-color: $user; border-color: $border; }
QTextBrowser#messageText { background-color: transparent; color: $text;
    selection-background-color: $highlight; selection-color: $selected_text; }
QPushButton#chatSuggestion, QPushButton#chatTool, QToolButton#themeButton {
    background-color: $button; color: $text; border-color: $border;
}
QPushButton#chatSuggestion:hover, QPushButton#chatTool:hover, QToolButton#themeButton:hover {
    background-color: $hover; border-color: $accent;
}
QPushButton#chatTool:disabled { color: $disabled; background-color: $card; }
QWidget#messageActions QPushButton { color: $muted; }
QWidget#messageActions[active="true"] QPushButton, QWidget#messageActions QPushButton:focus {
    background-color: $button; color: $text; border-color: $border;
}
QWidget#messageActions QPushButton:checked, QWidget#messageActions QPushButton:hover {
    background-color: $hover; color: $text;
}
QFrame#composer, QPlainTextEdit#chatInput { background-color: $input; color: $text; border-color: $border; }
QPushButton#cozySend { background-color: $accent; color: $selected_text; border-color: $accent; }
QPushButton#cozySend:hover { background-color: $highlight; }
QPushButton#cozySend:disabled { background-color: $button; color: $disabled; border-color: $border; }
"""
