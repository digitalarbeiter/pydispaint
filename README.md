# pydispaint
Distributed Paint Program


(!) Warning (!) This is very much under development.

Missing features (in order of perceived relevance):
- [x] concurrent clients (blocked by single-threaded HTTPServer)
- [x] fix re-draw issue on resize
- [x] some form of authentication
- [x] rejected: played around with SSL; certs are too much of a hassle
- [x] saving the image (other than screenshot, that is)
- [x] export/import server state (so we can continue a paining together)
- [x] delete a.k.a. the wide white marker
- [x] brush width selectable
- [ ] show other people's cursors
- [ ] packaging and distribution


# Installation and Start

    apt install wbritish-large  # for passphrases
    mkvirtualenv -p python3 pydispaint
    pip install -r requirements.txt
    python pydispaint.py --help

To start the server, run:

    python pydispaint.py server --help
    python pydispaint.py server

This will output the passphrase for this session.

To start the client (the actual paint program), run:

    python pydispaint.py paint --help
    python pydispaint.py paint --color blue --passphrase <pp>

