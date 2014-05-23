#! /usr/bin/python
# -*- coding: utf-8 -*-
import os
import sys
import json
import signal
import functools
import subprocess
from uuid import uuid4

import xcb
from xcb import xproto
from xcb.xproto import EventMask, GrabMode, unpack_from

from PyQt5 import QtCore
from PyQt5.QtCore import QCoreApplication
QCoreApplication.setAttribute(10, True)
QCoreApplication.setAttribute(QtCore.Qt.AA_X11InitThreads, True)

from PyQt5.QtQuick import QQuickView, QQuickItem
from PyQt5.QtWidgets import QApplication, qApp
from PyQt5.QtGui import (QSurfaceFormat, QColor, QKeySequence, QKeyEvent, 
    QCursor, QFont, QFontMetrics)
from PyQt5.QtCore import (QObject, Q_CLASSINFO, pyqtSlot, pyqtProperty, 
    pyqtSignal, QTimer)
from PyQt5.QtDBus import (QDBusAbstractAdaptor, QDBusConnection, 
    QDBusConnectionInterface, QDBusMessage, QDBusObjectPath)

from logger import func_logger, logger
from DBusInterfaces import (MenuObjectInterface, XMouseAreaInterface, 
    DisplayPropertyInterface)

def getWorkArea():
    conn = xcb.connect()
    setup = conn.get_setup()
    root = setup.roots[0].root

    cookie1 = conn.core.InternAtom(False, 13, "_NET_WORKAREA");
    cookie2 = conn.core.InternAtom(True, 8, "CARDINAL");
    name = cookie1.reply().atom
    type = cookie2.reply().atom

    cookie = conn.core.GetProperty(False, root, name, type, 0, 4)
    reply = cookie.reply()

    return unpack_from('IIII', reply.value.buf())
    
def getCursorPosition():
    return QCursor().pos()

def isInRect(x, y, xx, xy, xw, xh):
    return (xx <= x <= xx + xw) and (xy <= y <= xy + xh)

class MenuService(QObject):
    def __init__(self):
        super(MenuService, self).__init__()
        self.__dbusAdaptor = MenuServiceAdaptor(self)
        self._sessionBus = QDBusConnection.sessionBus()

        self.__menu = None

    def __checkToRestart(self):
        if self.__restart_flag:
            INJECTION.startNewService()
        self.__restart_flag = True
        del self.__timer

    def registerMenu(self):
        self.__restart_flag = False

        objPath = "/com/deepin/menu/%s" % str(uuid4()).replace("-", "_")
        objPathHolder= objPath.replace("/", "_")
        setattr(self, objPathHolder, MenuObject(self, objPath))
        self._sessionBus.registerObject(objPath, getattr(self, objPathHolder))
        result = QDBusObjectPath()
        result.setPath(objPath)
        return result

    def unregisterMenu(self, objPath):
        xgraber.unregisterXMouseArea()
        self.__restart_flag = True

        self._sessionBus.unregisterObject(objPath)
        objPathHolder= objPath.replace("/", "_")
        if self.__menu: self.__menu.ancestor.destroyForward()
        setattr(self, objPathHolder, None)
        msg = QDBusMessage.createSignal(objPath, 'com.deepin.menu.Menu', 
            'MenuUnregistered')
        QDBusConnection.sessionBus().send(msg)

        self.__timer = QTimer()
        self.__timer.singleShot = True
        self.__timer.timeout.connect(self.__checkToRestart)
        self.__timer.start(5000)

    def showMenu(self, dbusObj, menuJsonContent):
        if self.__menu:
            self.__menu.destroySubs()
            self.__menu.setDBusObj(dbusObj)
            self.__menu.setMenuJsonContent(menuJsonContent)
        else:
            self.__menu = Menu(dbusObj, menuJsonContent)
        xgraber.owner = self.__menu
        xgraber.registerXMouseArea()
        
        self.__menu.showMenu()
        self.__menu.requestActivate()

class MenuServiceAdaptor(QDBusAbstractAdaptor):
    """DBus service for creating beautiful menus."""

    Q_CLASSINFO("D-Bus Interface", "com.deepin.menu.Manager")
    Q_CLASSINFO("D-Bus Introspection",
                '  <interface name="com.deepin.menu.Manager">\n'
                '    <method name="RegisterMenu">\n'
                '      <arg direction="out" type="o" name="menuObjectPath"/>\n'
                '    </method>\n'
                '    <method name="UnregisterMenu">\n'
                '      <arg direction="in" type="s" name="menuObjectPath"/>\n'
                '    </method>\n'
                '  </interface>\n')

    def __init__(self, parent):
        super(MenuServiceAdaptor, self).__init__(parent)

    @pyqtSlot(result="QDBusObjectPath")
    def RegisterMenu(self):
        return self.parent().registerMenu()

    @pyqtSlot(str)
    def UnregisterMenu(self, objPath):
        return self.parent().unregisterMenu(objPath)

class MenuObject(QObject):
    def __init__(self, manager, objPath):
        super(MenuObject, self).__init__()
        self.__dbusAdaptor = MenuObjectAdaptor(self)
        self.manager = manager
        self.objPath = objPath

    def showMenu(self, menuJsonContent):
        self.manager.showMenu(self, menuJsonContent)

    def setItemText(self, id, value):
        msg = QDBusMessage.createSignal(self.objPath, 'com.deepin.menu.Menu', 
            'ItemTextSet')
        msg << id << value
        QDBusConnection.sessionBus().send(msg)

    def setItemActivity(self, id, value):
        msg = QDBusMessage.createSignal(self.objPath, 'com.deepin.menu.Menu', 
            'ItemActivitySet')
        msg << id << value
        QDBusConnection.sessionBus().send(msg)

    def setItemChecked(self, id, value):
        msg = QDBusMessage.createSignal(self.objPath, 'com.deepin.menu.Menu', 
            'ItemCheckedSet')
        msg << id << value
        QDBusConnection.sessionBus().send(msg)

class MenuObjectAdaptor(QDBusAbstractAdaptor):

    Q_CLASSINFO("D-Bus Interface", "com.deepin.menu.Menu")
    Q_CLASSINFO("D-Bus Introspection",
                '  <interface name="com.deepin.menu.Menu">\n'
                '    <method name="ShowMenu">\n'
                '      <arg direction="in" type="s" name="menuJsonContent"/>\n'
                '    </method>\n'
                '    <method name="SetItemActivity">\n'
                '      <arg direction="in" type="s" name="itemId"/>\n'
                '      <arg direction="in" type="b" name="isActive"/>\n'
                '    </method>\n'
                '    <method name="SetItemChecked">\n'
                '      <arg direction="in" type="s" name="itemId"/>\n'
                '      <arg direction="in" type="b" name="checked"/>\n'
                '    </method>\n'
                '    <method name="SetItemText">\n'
                '      <arg direction="in" type="s" name="itemId"/>\n'
                '      <arg direction="in" type="s" name="text"/>\n'
                '    </method>\n'
                '    <signal name="ItemInvoked">\n'
                '      <arg direction="out" type="s" name="itemId"/>\n'
                '      <arg direction="out" type="b" name="checked"/>\n'
                '    </signal>\n'
                '    <signal name="ItemTextSet">\n'
                '      <arg direction="out" type="s" name="itemId"/>\n'
                '      <arg direction="out" type="s" name="text"/>\n'
                '    </signal>\n'
                '    <signal name="ItemActivitySet">\n'
                '      <arg direction="out" type="s" name="itemId"/>\n'
                '      <arg direction="out" type="b" name="isActive"/>\n'
                '    </signal>\n'
                '    <signal name="ItemCheckedSet">\n'
                '      <arg direction="out" type="s" name="itemId"/>\n'
                '      <arg direction="out" type="b" name="checked"/>\n'
                '    </signal>\n'
                '    <signal name="MenuUnregistered">\n'
                '    </signal>\n'
                '  </interface>\n')

    ItemInvoked = pyqtSignal(str, bool)
    ItemTextSet = pyqtSignal(str, str)
    ItemActivitySet = pyqtSignal(str, bool)
    ItemCheckedSet = pyqtSignal(str, bool)
    MenuUnregistered = pyqtSignal()

    def __init__(self, parent):
        super(MenuObjectAdaptor, self).__init__(parent)

    @pyqtSlot(str)
    def ShowMenu(self, menuJsonContent):
        self.parent().showMenu(menuJsonContent)

    @pyqtSlot(str, str)
    def SetItemText(self, id, value):
        self.parent().setItemText(id, value)

    @pyqtSlot(str, bool)
    def SetItemActivity(self, id, value):
        self.parent().setItemActivity(id, value)

    @pyqtSlot(str, bool)
    def SetItemChecked(self, id, value):
        self.parent().setItemChecked(id, value)

class Injection(QObject):
    def __init__(self):
        super(QObject, self).__init__()

    @pyqtSlot(str,int,result=int)
    def getStringWidth(self, string, pixelSize):
        font = QFont()
        font.setPixelSize(pixelSize)
        fm = QFontMetrics(font)
        return fm.width(string)

    @pyqtSlot(str,int,result=int)
    def getStringHeight(self, string, pixelSize):
        font = QFont()
        font.setPixelSize(pixelSize)
        fm = QFontMetrics(font)
        return fm.height()

    @pyqtSlot(str, result=int)
    def keyStringToCode(self, s):
        seq = QKeySequence(s)
        if len(seq) > 0:
            return seq[0]
        return -1

    def startNewService(self):
        subprocess.Popen(["python", "-OO", 
            os.path.join(os.path.dirname(__file__), "main.py")])
        
class postGui(QtCore.QObject):
    
    throughThread = QtCore.pyqtSignal(object, object)    
    
    def __init__(self, inclass=True):
        super(postGui, self).__init__()
        self.throughThread.connect(self.onSignalReceived)
        self.inclass = inclass
        
    def __call__(self, func):
        self._func = func
        
        @functools.wraps(func)
        def objCall(*args, **kwargs):
            self.emitSignal(args, kwargs)
        return objCall
        
    def emitSignal(self, args, kwargs):
        self.throughThread.emit(args, kwargs)
                
    def onSignalReceived(self, args, kwargs):
        if self.inclass:
            obj, args = args[0], args[1:]
            self._func(obj, *args, **kwargs)
        else:    
            self._func(*args, **kwargs)

INJECTION = Injection()
LITERAL_KEYS = tuple("abcdefghijklmnopqrstuvwxyz")
CONTROL_KEYS = ("up", "down", "left", "right", "enter", "escape")
ALLOWED_KEYS = LITERAL_KEYS + CONTROL_KEYS

class XGraber(QObject):

    RegisterAreaMotionFlag = 1 << 0
    RegisterAreaButtonFlag = 1 << 1
    RegisterAreaKeyFlag = 1 << 2
    RegisterAreaAllFlag = (
        RegisterAreaMotionFlag | 
        RegisterAreaButtonFlag | 
        RegisterAreaKeyFlag )  

    def __init__(self, owner=None):
        super(QObject, self).__init__()
        self.owner = owner
        self._mousearea = XMouseAreaInterface()
        self._display = DisplayPropertyInterface()
        self._cookie = None
        self._conn = xcb.connect()

        self._mousearea.ButtonPress.connect(self.onButtonPress)
        self._mousearea.KeyPress.connect(self.onKeyPress)
        self._mousearea.MotionMove.connect(self.onMotionMove)

    @property
    def owner_wid(self):
        return self.owner.winId().__int__() if self.owner else None

    def registerXMouseArea(self):
        if not self._cookie: 
            rect = self.owner.rootObject().getCurrentMonitorRect()
            self._cookie = self._mousearea.registerArea(int(rect.x()),
                                                        int(rect.y()), 
                                                        int(rect.x() + rect.width()),
                                                        int(rect.y() + rect.height()),
                                                        XGraber.RegisterAreaAllFlag)

    def unregisterXMouseArea(self):
        if self._cookie: 
            self._mousearea.unregisterArea(self._cookie) 
            self._cookie = None

    @func_logger()
    def grab_pointer(self):
        if not self.owner_wid: return

        try_times = 200
        while try_times:
            mask = (EventMask.PointerMotion 
                | EventMask.ButtonRelease 
                | EventMask.ButtonPress)            
            grab_status = self._conn.core.GrabPointer(False, 
                self.owner_wid, 
                mask, GrabMode.Async, GrabMode.Async,
                0, 0,
                xproto.Time.CurrentTime).reply().status
            logger.debug("grab result: %s" % grab_status)
            if grab_status in [0, 1]: break
            try_times -= 1

    @func_logger()
    def ungrab_pointer(self):
        if not self.owner_wid: return
        self._conn.core.UngrabPointerChecked(xproto.Time.CurrentTime).check()

    @func_logger()
    def grab_keyboard(self):
        if not self.owner_wid: return
        
        try_times = 200        
        while try_times:
            grab_status = self._conn.core.GrabKeyboard(False, 
                self.owner_wid, xproto.Time.CurrentTime,
                GrabMode.Async, GrabMode.Async).reply().status
            logger.debug("grab result: %s" % grab_status)            
            if grab_status in [0, 1]: break
            try_times -= 1

    @func_logger()
    def ungrab_keyboard(self):
        if not self.owner_wid: return
        self._conn.core.UngrabKeyboardChecked(xproto.Time.CurrentTime).check()
        
    @postGui()
    def simulate_key_press(self, keycode):
        qt_control_keys = (16777235, 16777237, 16777234, 
            16777236, 16777220, 16777216)
        qt_ascii_keys = range(65, 91)
        if keycode in LITERAL_KEYS:
            keycode = qt_ascii_keys[LITERAL_KEYS.index(keycode)]
        elif keycode in CONTROL_KEYS:
            keycode = qt_control_keys[CONTROL_KEYS.index(keycode)]
        key_press_event = QKeyEvent(
            QtCore.QEvent.KeyPress,
            keycode,
            QtCore.Qt.NoModifier)
        self.owner.focusOwner.sendEvent(
            self.owner.focusOwner.rootObject().findChild(QQuickItem, "listview")
            , key_press_event)

    def onButtonPress(self, button, x, y, cookie):
        print("onButtonPress")
        if cookie == self._cookie: 
            if self.owner and not self.owner.inMenuArea(x, y):
                self.ungrab_pointer()
                self.ungrab_keyboard()
                self.owner.destroyWholeMenu()                

    def onKeyPress(self, key, x, y, cookie):
        print("onKeyPress")
        if cookie == self._cookie:
            if self.owner and key in ALLOWED_KEYS:
                self.simulate_key_press(key)

    def onMotionMove(self, x, y, cookie):
        print("onMotoinMove")
        if cookie == self._cookie:
            if self.owner:
                if self.owner.inMenuArea(x, y):
                    self.ungrab_pointer()
                else:
                    self.grab_pointer()

class Menu(QQuickView):
    def __init__(self, dbusObj, menuJsonContent, parent=None):
        QQuickView.__init__(self)
        self.parent = parent
        self.subMenu = None
        
        qml_context = self.rootContext()
        qml_context.setContextProperty("_menu_view", self)
        qml_context.setContextProperty("_injection", INJECTION)

        surface_format = QSurfaceFormat()
        surface_format.setAlphaBufferSize(8)
        self.setFormat(surface_format)
        self.setColor(QColor(0, 0, 0, 0))
        self.setFlags(QtCore.Qt.Tool
                      | QtCore.Qt.X11BypassWindowManagerHint
                      | QtCore.Qt.WindowStaysOnTopHint)
        
        self.setMenuJsonContent(menuJsonContent)
        self.setDBusObj(dbusObj)
        
        # self.installEventFilter(self)
        qApp.focusWindowChanged.connect(self.focusWindowChangedSlot)

    def setDBusObj(self, dbusObj):
        self.dbusObj = dbusObj
        self.dbus_interface = MenuObjectInterface(self.dbusObj.objPath)
        self.dbus_interface.ItemTextSet.connect(self.updateItemText)
        self.dbus_interface.ItemActivitySet.connect(self.updateItemActivity)
        self.dbus_interface.ItemCheckedSet.connect(self.updateCheckableItem)

    def setMenuJsonContent(self, menuJsonContent):
        self.__menuJsonContent = menuJsonContent
        
        self.setX(self.menuJsonObj["x"])
        self.setY(self.menuJsonObj["y"])

        if self.menuJsonObj["isDockMenu"] and not self.isSubMenu:
            self.setSource(QtCore.QUrl.fromLocalFile(
                os.path.join(os.path.dirname(__file__), 
                'DockMenu.qml')))
        else:
            self.setSource(QtCore.QUrl.fromLocalFile(
                os.path.join(os.path.dirname(__file__), 
                'RectMenu.qml')))

    def focusWindowChangedSlot(self, window):
        if not self:
            return
        if window == None:
            self.ungrab_pointer()
            self.ungrab_keyboard()
            self.destroyWholeMenu()
            
    def inMenuArea(self, x, y):
        if isInRect(x, y, self.x(), self.y(), self.width(), self.height()):
            return True
        elif self.subMenu and self.subMenu.inMenuArea(x, y):
            return True
        return False

    # def eventFilter(self, obj, event):
        # cursor_pos = getCursorPosition()
        # if isinstance(obj, Menu) and event.type() == QEvent.Leave:
        #     if not self.ancestor.inMenuArea(cursor_pos.x(), cursor_pos.y()):
        #         print "grab here"
        #         self.grab_pointer()
        #         self.grab_keyboard()
        #     else:
        #         self.ungrab_pointer()
        #         self.ungrab_keyboard()
        # return QWidget.eventFilter(self, obj, event)
        
    def set_menu_hint(self):
        conn = xcb.connect()
        conn.core.ChangeProperty(
            xproto.PropMode.Replace,
            self.winId().__int__(),
            conn.core.InternAtom(False, 
                                 len("_NET_WM_WINDOW_TYPE"), 
                                 "_NET_WM_WINDOW_TYPE").reply().atom,
            xproto.Atom.ATOM,
            32,
            2,
            conn.core.InternAtom(False, 
                                 len("_NET_WM_WINDOW_TYPE_POP_MENU"), 
                                 "_NET_WM_WINDOW_TYPE_POP_MENU").reply().atom
        )
        conn.disconnect()
        
    def grab_pointer(self):
        xgraber.grab_pointer()
        
    def ungrab_pointer(self):
        xgraber.ungrab_pointer()

    def grab_keyboard(self):
        xgraber.grab_keyboard()
        
    def ungrab_keyboard(self):
        xgraber.ungrab_keyboard()

    @property
    def ancestor(self):
        if not self.parent:
            return self
        else: return self.parent.ancestor
        
    @property
    def focusOwner(self):
        this = self
        while this:
            if this.isActive():
                return this
            this = this.subMenu
        return self

    @pyqtProperty(bool)
    def isSubMenu(self):
        return not self.parent == None

    @pyqtProperty(str)
    def menuJsonContent(self):
        return self.__menuJsonContent

    @pyqtProperty("QVariant", constant=True)
    def menuJsonObj(self):
        return json.loads(self.__menuJsonContent)

    @pyqtSlot(str, bool)
    def notifyUpdateItemChecked(self, id, value):
        self.dbus_interface.setItemChecked(id, value)

    def updateCheckableItem(self, id, value):
        self.rootObject().updateCheckableItem(id, value)

    def updateItemActivity(self, id, value):
        self.rootObject().updateItemActivity(id, value)

    def updateItemText(self, id, value):
        self.rootObject().updateItemText(id, value)

    @pyqtSlot(str, bool)
    def invokeItem(self, id, checked):
        msg = QDBusMessage.createSignal(self.dbusObj.objPath, 
            'com.deepin.menu.Menu', 'ItemInvoked')
        msg << id << checked
        QDBusConnection.sessionBus().send(msg)

    @pyqtSlot()
    def activateParent(self):
        if self.parent != None:
            self.parent.requestActivate()

    @pyqtSlot()
    def activateSubMenu(self):
        if self.subMenu != None:
            self.subMenu.requestActivate()
            self.subMenu.rootObject().selectItem(0)

    @pyqtSlot(str)
    def showSubMenu(self, menuJsonContent):
        if menuJsonContent and self.isVisible():
            self.subMenu = Menu(self.dbusObj, menuJsonContent, self)
            self.subMenu.showMenu()
        else:
            self.subMenu = None

    def showMenu(self):
        self.show()
        self.grab_pointer()
        self.grab_keyboard()
        
    @postGui()
    def destroyForward(self):
        if self.subMenu:
            self.subMenu.destroyForward()
        
        # signal.signal(signal.SIGALRM, lambda signum, frame: os._exit(0))
        # signal.alarm(1)
        self.close()
        # signal.alarm(0)
        
    @pyqtSlot()
    def destroyWholeMenu(self):
        menuService.unregisterMenu(self.dbusObj.objPath)
        
    @pyqtSlot()
    def destroySubs(self):
        if self.subMenu: self.subMenu.destroyForward()
        self.requestActivate()

@pyqtSlot(str)
def serviceReplacedByOtherSlot(name):
    os._exit(0 and xgraber.unregisterXMouseArea())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    xgraber = XGraber()

    menuService = MenuService()
    bus = QDBusConnection.sessionBus()
    bus.interface().registerService('com.deepin.menu',
        QDBusConnectionInterface.ReplaceExistingService,
        QDBusConnectionInterface.AllowReplacement)
    bus.registerObject('/com/deepin/menu', menuService)
    bus.interface().serviceUnregistered.connect(serviceReplacedByOtherSlot)

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(app.exec_() and xgraber.unregisterXMouseArea())
