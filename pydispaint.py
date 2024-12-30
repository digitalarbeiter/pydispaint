# pylint: disable=invalid-name,no-name-in-module,import-error
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

import click
import requests
from loguru import logger

from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget
from PyQt5.QtGui import QPainter, QPen, QImage, QColor, QCursor, QPixmap, QPolygon
from PyQt5.QtCore import Qt, QPoint


# available colors

COLORS = [
    ("black", QColor(0, 0, 0)),
    ("red", QColor(255, 0, 0)),
    ("green", QColor(0, 255, 0)),
    ("blue", QColor(0, 0, 255)),
]


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
    def __init__(self, *, server, port, update_interval, color):  # pylint: disable=redefined-outer-name
        super().__init__()
        self.setWindowTitle("Drawing App")
        self.setGeometry(100, 100, 800, 600)
        self.canvas = Canvas(self, color)
        self.setCentralWidget(self.canvas)
        if server and port:
            self.session = requests.Session()
            self.base_url = f"http://{server}:{port}"
            self.startTimer(update_interval)
        else:
            self.session = None
            self.base_url = None

    def send_paintcode(self, *, start_point, end_point, color, width):
        if self.session and start_point != end_point:
            start_point = encode_point(start_point)
            end_point = encode_point(end_point)
            color = encode_color(color)
            self.session.get(f"{self.base_url}/draw?color={color}&width={width}&start_point={start_point}&end_point={end_point}")

    def timerEvent(self, _event):
        if self.session:
            resp = self.session.get(f"{self.base_url}/updates")
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


class PaintRequestHandler(BaseHTTPRequestHandler):
    """ Dispatcher for the Paint Server.
    """
    def __init__(self, request, client_address, server):  # pylint: disable=redefined-outer-name
        self.protocol_version = "HTTP/1.1"  # needed for keep-alive
        super().__init__(request, client_address, server)

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
        if url.path == "/status":
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
                self.server.get_updates(client=self.client_address),
            )
        else:
            self.respond(400, f"unknown path: {url.path}")


class PaintNet(ThreadingMixIn, HTTPServer):
    """ Paint Server. Manages image paint events and distributes updates
        to different clients.
    """
    def __init__(self, *, server, port):  # pylint: disable=redefined-outer-name
        super().__init__((server, port), PaintRequestHandler)
        self.server = server
        self.port = port
        self.painting = []
        self.client_states = {}

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
        logger.info(f"draw {start_point} -> {end_point} (client: {client})")
        self.painting.append({
            "color": color,
            "width": int(width),
            "start_point": start_point,
            "end_point": end_point,
        })
        return self.get_updates(client)

    def get_updates(self, client):
        len_painting = len(self.painting)
        start = self.client_states.get(client, 0)
        self.client_states[client] = len_painting
        return json.dumps(self.painting[start:])

    def exec_(self):
        logger.info("serving forever...")
        self.serve_forever()
        return 0


@click.group()
def cli():
    ...


@cli.command("server")
@click.option("--port", type=int, default=8088)
def server(port):
    app = PaintNet(server="", port=port)
    sys.exit(app.exec_())


@cli.command("paint")
@click.option("--server", default="localhost")
@click.option("--port", type=int, default=8088)
@click.option("--color", type=click.Choice([cname for cname, _qcolor in COLORS]))
@click.option("--interval", type=int, default=50, help="poll interval in ms")
def paint(server, port, interval, color):  # pylint: disable=redefined-outer-name
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
        update_interval=interval,
        color=color,
    )
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    cli()
