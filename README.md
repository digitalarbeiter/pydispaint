# pydispaint
Distributed Paint Program


(!) Warning (!) This is very much under development.

Missing features (in order of perceived relevance):
- [✓] concurrent clients (blocked by single-threaded HTTPServer)
- [✓] fix re-draw issue on resize
- [ ] some form of authentication
- [ ] saving the image (other than screenshot, that is)
- [ ] show other people's cursors
- [ ] packaging and distribution


# Installation and Start

    mkvirtualenv -p python3 pydispaint
    pip install -r requirements.txt
    python pydispaint.py --help

To start the server, run:

    python pydispaint.py server --help
    python pydispaint.py server

To start the client (the actual paint program), run:

    python pydispaint.py paint --help
    python pydispaint.py paint --color blue

