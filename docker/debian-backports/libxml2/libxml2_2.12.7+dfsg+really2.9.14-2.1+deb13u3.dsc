-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Format: 3.0 (quilt)
Source: libxml2
Binary: libxml2, libxml2-utils, libxml2-dev, libxml2-doc, python3-libxml2
Architecture: any all
Version: 2.12.7+dfsg+really2.9.14-2.1+deb13u3
Maintainer: Debian XML/SGML Group <debian-xml-sgml-pkgs@lists.alioth.debian.org>
Uploaders: Aron Xu <aron@debian.org>, YunQiang Su <wzssyqa@gmail.com>
Homepage: http://xmlsoft.org
Description: GNOME XML library
 XML is a metalanguage to let you design your own markup language.
 A regular markup language defines a way to describe information in
 a certain class of documents (eg HTML). XML lets you define your
 own customized markup languages for many classes of document. It
 can do this because it's written in SGML, the international standard
 metalanguage for markup languages.
Standards-Version: 4.6.0
Vcs-Browser: https://salsa.debian.org/xml-sgml-team/libxml2
Vcs-Git: https://salsa.debian.org/xml-sgml-team/libxml2.git
Testsuite: autopkgtest
Testsuite-Triggers: build-essential, pkg-config
Build-Depends: debhelper-compat (= 13)
Build-Depends-Arch: dh-sequence-python3 <!nopython>, liblzma-dev, libpython3-all-dev <!nopython>, pkg-config, python3-all-dev:any (>= 3.5) <!nopython>, python3-setuptools <!nopython>, rename, zlib1g-dev | libz-dev
Build-Depends-Indep: pkg-config
Package-List:
 libxml2 deb libs optional arch=any
 libxml2-dev deb libdevel optional arch=any
 libxml2-doc deb doc optional arch=all
 libxml2-utils deb text optional arch=any
 python3-libxml2 deb python optional arch=any profile=!nopython profile:v1=!nopython
Checksums-Sha1:
 b41615e638174b4e36845c68d4b305dd6a6b541f 2351200 libxml2_2.12.7+dfsg+really2.9.14.orig.tar.xz
 1d87d64579a19726bc00c1dd2d25dc85384d9586 58040 libxml2_2.12.7+dfsg+really2.9.14-2.1+deb13u3.debian.tar.xz
Checksums-Sha256:
 4fe913dec8b1ab89d13b489b419a8203176ea39e931eaa0d25b17eafb9c279e9 2351200 libxml2_2.12.7+dfsg+really2.9.14.orig.tar.xz
 3b6d265f482d6a8fbe3c056d2006fb3b563b4a838f7258b388ac5f0b29206921 58040 libxml2_2.12.7+dfsg+really2.9.14-2.1+deb13u3.debian.tar.xz
Files:
 bbcae2f48d1c9b1413ef953ce87e9346 2351200 libxml2_2.12.7+dfsg+really2.9.14.orig.tar.xz
 252478c7b538ca7c167d96b746442ff2 58040 libxml2_2.12.7+dfsg+really2.9.14-2.1+deb13u3.debian.tar.xz

-----BEGIN PGP SIGNATURE-----

iQIzBAEBCgAdFiEERpy6p3b9sfzUdbME05pJnDwhpVIFAmonHhMACgkQ05pJnDwh
pVKzKQ/8DzrEwFL/GtaGaPTvQ1EkaPvOk0zEaIB7aO1xwf2j9uRYEItt7w9OqTdk
tSl2YYUCsV3a1kxsvhfNMbe/PIkW1VUQ3tMVPKAlQWp4rdzfk5+k52pclTuhknZd
XlzuTyg+r38scefCkHJ5+BhjIG6f0DChk6Epw1BNKZuC0KhTuEJ59S0Gq6E6gREb
Am4MCmybydkEt8Jhc/fUYsLTOytaxqeJMi4lyttBfAIT9hT1Nx1UHoAyf9JvCw5L
bkhcWTsOzLwry3Q++lpwiPLLUfNz7ZZ6BPLPjzBcJ/mVlYccFEsB4LT6EC71RBs8
XvFYrtEv9ognMyahPHU90i7wLShAGtmIbjXMfNpSNCnPNiMRJQK+v9uZVQnr8vwN
EGJ8NqtmpfRxZFfKqMTgelGJXisGPqt0y29R2nbrTT5+ybjNL6pupUb7RtWgDt8L
NvOYPXbFGXdPzREmlLPFo4aE/pU5dEkrA0y17zmoTzD1rfNcEQKJYX8vFECJyyaZ
FPte21s8rF1nanqc91ytLTkfGTK45tU4uCXT+TLvgZZdxgGxSxGoSkYfkZt+sA3+
K8xs+H7MTN4HPjJfWD0T3Nk6GOO6pa7V63M+4v/5lPSstQFEI7EgdFXGu0YtxRkR
nskNXhGUkrfLSLO3Nk24ek9JwIjtj63grtLemEgtqjigb+1sn5Q=
=d5rd
-----END PGP SIGNATURE-----
