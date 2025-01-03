# pylint: disable=invalid-name,no-name-in-module,import-error
import json
import logging  # for setting requests log level
import random
import socket
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie, CookieError
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

import click
import requests
from loguru import logger

from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QMessageBox, QAction, QFileDialog
from PyQt5.QtGui import QPainter, QPen, QImage, QColor, QCursor, QPixmap, QPolygon
from PyQt5.QtCore import Qt, QPoint


# available colors

COLORS = [
    ("black", QColor(0, 0, 0)),
    ("red", QColor(255, 0, 0)),
    ("green", QColor(0, 255, 0)),
    ("blue", QColor(0, 0, 255)),
]
COLOR_NAMES = [cname for cname, _qcolor in COLORS]



# server conventions

def encode_color(qcolor):
    r, g, b, _alpha = qcolor.getRgb()
    return f"{r},{g},{b}"

def decode_color(color_str):
    r, g, b = color_str.split(",")
    return QColor(int(r), int(g), int(b))

def encode_point(qpoint):
    x, y = qpoint.x(), qpoint.y()
    return f"{x},{y}"

def decode_point(point_str):
    x, y = point_str.split(",")
    return QPoint(int(x), int(y))


class Canvas(QWidget):
    """ Drawing Widget.
        Reacts to painting and mouse wheel color switching.
        Sends paint events to `app` for network transmission.
    """
    def __init__(self, app, color):
        super().__init__()
        self.app = app
        self.color = color
        self.setAttribute(Qt.WA_StaticContents)
        self.drawing = False
        self.last_point = QPoint()
        self.image = QImage(self.size(), QImage.Format_RGB32)
        self.image.fill(Qt.white)
        self.set_pen_cursor()

    def paintEvent(self, event):  # pylint: disable=unused-argument
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.image, self.image.rect())

    def wheelEvent(self,event):
        steps = event.angleDelta().y() // 120
        current_color = 0
        for current_color, (_, qcolor) in enumerate(COLORS):
            if qcolor == self.color:
                break
        new_color = (current_color + steps) % len(COLORS)
        logger.debug(f"switch color from {COLORS[current_color][0]} to {COLORS[new_color][0]}")
        self.color = COLORS[new_color][1]
        self.set_pen_cursor()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = True
            self.last_point = event.pos()

    def mouseMoveEvent(self, event):
        if self.drawing and event.buttons() & Qt.LeftButton:
            self.draw_line(
                start_point=self.last_point,
                end_point=event.pos(),
                color=self.color,
                width=3,
            )
            self.update()
            self.app.send_paintcode(
                start_point=self.last_point,
                end_point=event.pos(),
                color=self.color,
                width=3,
            )
            self.last_point = event.pos()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = False

    def resizeEvent(self, event):
        new_image = QImage(self.size(), QImage.Format_RGB32)
        new_image.fill(Qt.white)
        painter = QPainter(new_image)
        painter.drawImage(0, 0, self.image)
        self.image = new_image
        super().resizeEvent(event)
        self.app.update_from_beginning = True  # next timer redraws all

    def draw_line(self, *, start_point, end_point, color, width):
        if start_point == end_point:
            return
        logger.debug(f"draw line {start_point.x()},{start_point.y()} to {end_point.x()},{end_point.y()} in #{hex(color.rgb())[4:]} {width}pt.")
        painter = QPainter(self.image)
        pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(start_point, end_point)

    def set_pen_cursor(self):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setPen(Qt.black)
        painter.setBrush(self.color)
        painter.drawRect(10, 10, 8, 20)  # Rectangle for the pen body
        painter.setBrush(self.color)
        painter.drawPolygon(
            QPolygon([
                QPoint(10, 10),  # Top-left corner of the pen body
                QPoint(18, 10),  # Top-right corner of the pen body
                QPoint(14, 0)    # Tip of the pen
            ])
        )
        painter.end()
        cursor = QCursor(pixmap, 14, 0)  # Hot spot at the tip of the pen
        self.setCursor(cursor)


class DrawingApp(QMainWindow):
    """ The Qt app is resonsible for doing all the network stuff on
        behalf of the canvas; for this it will be commanded by the
        canvas to send drawing instructions over the network, as well
        as send network updates to the canvas.
    """
    def __init__(self, *, server, port, passphrase, update_interval, color):
        super().__init__()
        self.passphrase = passphrase
        self.update_from_beginning = False
        self.setWindowTitle("Distributed Paint")
        self.setGeometry(100, 100, 800, 600)
        self.filename = None
        self.canvas = Canvas(self, color)
        self.setCentralWidget(self.canvas)
        if server and port:
            self.session = requests.Session()
            self.session.cookies.set("passphrase", self.passphrase)
            self.base_url = f"http://{server}:{port}"
            self.startTimer(update_interval)
        else:
            self.session = None
            self.base_url = None

        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")
        self.save_action = QAction("Save", self)
        self.save_action.triggered.connect(self.save)
        self.save_action.setDisabled(True)
        file_menu.addAction(self.save_action)
        save_as_action = QAction("Save as", self)
        save_as_action.triggered.connect(self.save_as)
        file_menu.addAction(save_as_action)
        new_action = QAction("Clear whiteboard", self)
        new_action.triggered.connect(self.clear)
        file_menu.addAction(new_action)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        help_menu = menu_bar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def timerEvent(self, _event):
        self.process_updates(from_beginning=self.update_from_beginning)
        self.update_from_beginning = False

    def save(self):
        self.canvas.image.save(self.filename)

    def save_as(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Image", "", "PNG(*.png);;JPEG(*.jpg *.jpeg);;All Files(*.*) ")
        if file_path == "":
            return
        self.filename = file_path
        self.save()
        self.setWindowTitle(f"Distributed Paint - {self.filename}")
        self.save_action.setEnabled(True)

    def clear(self):
        logger.debug("clearing whiteboard")
        self.session.get(f"{self.base_url}/new")
        self.canvas = Canvas(self, self.canvas.color)  # re-use old color
        self.setCentralWidget(self.canvas)
        self.update_from_beginning = True
        self.filename = None
        self.setWindowTitle("Distributed Paint")
        self.save_action.setDisabled(True)

    def show_about_dialog(self):
        QMessageBox.information(
            self,
            "About",
            f"Distributed Painting Program.\nPassphrase: {self.passphrase}\nhttps://github.com/digitalarbeiter/pydispaint",
        )

    def send_paintcode(self, *, start_point, end_point, color, width):
        if self.session and start_point != end_point:
            start_point = encode_point(start_point)
            end_point = encode_point(end_point)
            color = encode_color(color)
            self.session.get(f"{self.base_url}/draw?color={color}&width={width}&start_point={start_point}&end_point={end_point}")

    def process_updates(self, from_beginning=False):
        if self.session:
            resp = self.session.get(f"{self.base_url}/updates{'?from_beginning=1' if from_beginning else ''}")
            paint_codes = resp.json()
            if not paint_codes:
                return
            logger.info(f"have to paint {len(paint_codes)} events")
            for pc in paint_codes:
                self.canvas.draw_line(
                    start_point=decode_point(pc["start_point"]),
                    end_point=decode_point(pc["end_point"]),
                    color=decode_color(pc["color"]),
                    width=pc["width"],
                )
            self.canvas.update()
            logger.info(f"painted {len(paint_codes)} events")


class PaintRequestHandler(BaseHTTPRequestHandler):
    """ Dispatcher for the Paint Server.
    """
    def __init__(self, request, client_address, server):
        self.protocol_version = "HTTP/1.1"  # needed for keep-alive
        super().__init__(request, client_address, server)

    def log_request(self, code="-", size="-"):
        logger.info(f"{self.client_address[0]}:{self.client_address[1]} \"{self.requestline}\" {code} {size}")

    def respond(self, http_status, content):
        content = content.encode()
        self.send_response(http_status)
        # if we want keep-alive we need content length
        self.send_header("Connection", "Keep-Alive")
        self.send_header("Content-Length", f"{len(content)}")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query, keep_blank_values=True)
        try:
            cookies = SimpleCookie(self.headers.get("Cookie"))
            cookies["passphrase"].value  # pylint: disable=pointless-statement
        except CookieError:
            self.respond(
                401,
                json.dumps({"error": "bad cookie, what is wrong with you!"}),
            )
            return
        if cookies["passphrase"].value != self.server.passphrase:
            logger.error(f"wrong passphrase in cookies: {cookies['passphrase'].value}")
            self.respond(
                401,
                json.dumps({"error": "not authorized"}),
            )
        elif url.path == "/status":
            self.respond(
                200,
                self.server.status(),
            )
        elif url.path == "/new":
            self.respond(
                200,
                self.server.new(client=self.client_address),
            )
        elif url.path == "/draw":
            self.respond(
                200,
                self.server.draw(
                    color=query["color"][0],
                    width=query["width"][0],
                    start_point=query["start_point"][0],
                    end_point=query["end_point"][0],
                    client=self.client_address,
                ),
            )
        elif url.path == "/updates":
            self.respond(
                200,
                self.server.get_updates(
                    client=self.client_address,
                    from_beginning="from_beginning" in query,
                ),
            )
        else:
            logger.warning(f"invalid request: {self.path} ({url})")
            self.respond(400, f"unknown path: {url.path}")


# need debian package wbritish-large
try:
    WORDLIST = [
        word.strip()
        for word in open("/usr/share/dict/british-english-large")
        if "'" not in word and word[0].islower() and word.isascii()
    ]
except FileNotFoundError:
    # just a selection of 99 random words
    WORDLIST = [
        "abstractions", "allspice", "anisometropias", "associative", "banderilla",
        "brainteasers", "breaststrokes", "broadbills", "brooders", "cageling",
        "captives", "carven", "cellulosic", "chapels", "cheerless", "chemosmosis",
        "classmates", "clock", "corollary", "corrupting", "cucurbits", "culpably",
        "deciles", "declaratory", "deerhounds", "digression", "diminishes",
        "ectomere", "educe", "elegance", "eloign", "encompassment", "engender",
        "ensheathe", "erosion", "eurhythmics", "expiry", "extirpator", "eyebrow",
        "fourth", "fractiously", "gourmet", "graben", "grips", "hardships",
        "headachy", "heterochromosome", "hydride", "immemorially", "injuriousness",
        "insectivore", "laconically", "latte", "libidinal", "limitable",
        "logicians", "longshoremen", "lounges", "masterships", "nonjuror",
        "nystatins", "obstructionism", "onerousness", "outproduce", "overprotect",
        "parsimoniousnesses", "physiognomic", "pikestaff", "pituri", "pouncer",
        "prebendary", "pressies", "proficient", "psychotropic", "pudgy",
        "pyrexias", "questioned", "reefers", "residues", "reticently", "rotunda",
        "shikari", "signalisation", "speechmakers", "spelled", "starflower",
        "steamily", "sucks", "surplusages", "tinge", "transects", "uncaught",
        "underscoring", "unsuspecting", "versify", "voting", "weasels",
        "whichever", "wordiest",
    ]


class PaintNet(ThreadingMixIn, HTTPServer):
    """ Paint Server. Manages image paint events and distributes updates
        to different clients.
    """
    def __init__(self, *, server, port):
        super().__init__((server, port), PaintRequestHandler)
        self.server = server
        self.port = port
        self.painting = []
        self.client_states = {}
        self.passphrase = "-".join(random.choices(WORDLIST, k=3))

    def status(self):
        len_painting = len(self.painting)
        return json.dumps({
            "clients": [
                {
                    "client": c,
                    "state": s,
                    "lag": len_painting - s,
                }
                for c, s in self.client_states.items()
            ],
            "len-painting": len_painting,
        }, sort_keys=True, indent=4)

    def new(self, client):
        logger.info(f"new canvas (client: {client})")
        self.painting = []
        self.client_states = {
            client: 0 for client in self.client_states.keys()
        }
        self.client_states[client] = 0
        return json.dumps(self.painting)

    def draw(self, client, *, color, width, start_point, end_point):
        logger.debug(f"draw {start_point} -> {end_point} (client: {client})")
        self.painting.append({
            "color": color,
            "width": int(width),
            "start_point": start_point,
            "end_point": end_point,
        })
        return self.get_updates(client, from_beginning=False)

    def get_updates(self, client, from_beginning):
        len_painting = len(self.painting)
        if from_beginning:
            self.client_states.pop(client, None)
        start = self.client_states.get(client, 0)
        self.client_states[client] = len_painting
        return json.dumps(self.painting[start:])

    def exec_(self):
        logger.warning(f"passphrase: {self.passphrase}")
        logger.info("serving forever...")
        self.serve_forever()
        return 0


@click.group(context_settings={"show_default": True})
@click.option("-v", is_flag=True, help="verbose output")
@click.option("-vv", is_flag=True, help="very verbose output")
def cli(v, vv):
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    fmt = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> <level>{level:5} {message}</level>"
    logger.remove()
    if vv:
        logger.add(sys.stderr, colorize=True, format=fmt, level="DEBUG")
    elif v:
        logger.add(sys.stderr, colorize=True, format=fmt, level="INFO")
    else:
        logger.add(sys.stderr, colorize=True, format=fmt, level="WARNING")


@cli.command("server", context_settings={"show_default": True})
@click.option("--port", type=int, default=8088)
def server_command(port):
    """ Run dispaint server on given --port.
    """
    app = PaintNet(server=socket.getfqdn(), port=port)
    sys.exit(app.exec_())


@cli.command("paint", context_settings={"show_default": True})
@click.option("--server", default="localhost", help="dispaint server (host)")
@click.option("--port", type=int, default=8088, help="dispaint server (port)")
@click.option("--passphrase", required=True, help="server passphrase")
@click.option("--color", type=click.Choice(COLOR_NAMES), default="black")
@click.option("--interval", type=int, default=50, help="poll interval in ms")
def paint_command(server, port, passphrase, interval, color):
    """ Let's paint some stuff.
    """
    for cname, qcolor in COLORS:
        if color == cname:
            color = qcolor
            break
    else:
        color = QColor(0, 0, 0)
    app = QApplication(sys.argv)
    window = DrawingApp(
        server=server,
        port=port,
        passphrase=passphrase,
        update_interval=interval,
        color=color,
    )
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    cli()
