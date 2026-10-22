#!/bin/sh
# Probe the API image's ImageMagick policy from inside the image.
#
# scripts/verify_native_media_components.ts runs this as the aelira user with
# no network, a read-only root and an executable tmpfs on /tmp, and checks
# every "probe", "graphic", "latexml" and "gs" line it prints. The script only
# reports outcomes; it does not decide whether they are acceptable.
#
# Refused reads are paired with controls that run with an extra policy file in
# $HOME/.config/ImageMagick allowing those coders, so each refusal is shown to
# come from the shipped policy rather than from a broken probe.
set -u
umask 077
F=/tmp/aelira-im-probe
rm -rf "$F" && mkdir -p "$F/bin" "$F/home" "$F/override/.config/ImageMagick" "$F/w"
export HOME="$F/home"
export MAGICK_TIME_LIMIT=50

python3 -I - "$F" <<'PY'
import os, struct, sys, zlib

out = sys.argv[1]


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(width, height):
    pixels = zlib.compressobj(9)
    data = b"".join(pixels.compress(b"\x00" + b"\x00\x80\xff" * width) for _ in range(height)) + pixels.flush()
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", data) + chunk(b"IEND", b""))


def write(name, data):
    with open(os.path.join(out, name), "wb") as handle:
        handle.write(data)


write("control.png", png(8, 6))
write("edge.png", png(16000, 1))
write("wide.png", png(16001, 1))
write("tall.png", png(1, 16001))
content = b"0.1 0.6 0.9 0.05 k 0 0 72 48 re f"
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 48] /Contents 4 0 R"
    b" /Resources << /ColorSpace << /CS0 /DeviceCMYK >> >> >>",
    b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
]
pdf, offsets = b"%PDF-1.4\n", []
for number, body in enumerate(objects, 1):
    offsets.append(len(pdf))
    pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"
xref = len(pdf)
pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
write("cmyk.pdf", pdf)
write("cmyk.eps", b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 72 48\n"
      b"%%DocumentProcessColors: Cyan Magenta Yellow Black\n%%EndComments\n"
      b"0.1 0.6 0.9 0.05 setcmykcolor 0 0 72 48 rectfill\nshowpage\n%%EOF\n")
write("huge.eps", b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 16001 8\n%%EndComments\n"
      b"0 0 1 setrgbcolor 0 0 16001 8 rectfill\nshowpage\n%%EOF\n")
write("postscript.png", b"%!PS-Adobe-3.0\n%%BoundingBox: 0 0 72 48\n0 0 1 setrgbcolor 0 0 72 48 rectfill showpage\n")
write("probe.ps", b"%!PS\n0 0 1 setrgbcolor 0 0 10 10 rectfill showpage\n")
write("mvg-probe.png", b"push graphic-context\nviewbox 0 0 1 1\npop graphic-context\n")
write("probe.mvg", b"push graphic-context\nviewbox 0 0 1 1\npop graphic-context\n")
write("probe.msl", b'<?xml version="1.0"?><image><read filename="' + out.encode() + b'/control.png"/></image>\n')
write("probe.txt", b"# ImageMagick pixel enumeration: 1,1,255,srgb\n0,0: (255,0,0)  #FF0000  red\n")
write("probe.text", b"hello\n")
write("probe.cut", b"\x02\x00\x01\x00\x00\x00\x04\x00\x02\x05\x06\x00")
write("probe.svg", b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>\n')
write("list.txt", out.encode() + b"/control.png\n")
PY

cat > "$F/override/.config/ImageMagick/policy.xml" <<'XML'
<policymap>
  <policy domain="coder" rights="read" pattern="{MSL,MVG,CUT,TEXT,TXT,PS}"/>
</policymap>
XML

# Classify one command: accepted, policy:<NAME> for a security policy
# refusal, unregistered for a format ImageMagick does not have, limit for a
# resource limit, invalidfileaccess for a Ghostscript SAFER refusal, or other.
probe() {
  name=$1
  shift
  output=$("$@" 2>&1)
  status=$?
  if [ "$status" -eq 0 ]; then
    result=accepted
  else
    denied=$(printf '%s\n' "$output" | sed -n "s/.*not allowed by the security policy \`\([^']*\)'.*/\1/p" | head -n 1)
    if [ -n "$denied" ]; then
      result="policy:$denied"
    elif printf '%s\n' "$output" | grep -Eq 'no (decode|encode) delegate for this image format'; then
      result=unregistered
    elif printf '%s\n' "$output" | grep -q 'invalidfileaccess'; then
      result=invalidfileaccess
    elif printf '%s\n' "$output" | grep -Eqi 'exceeds (user )?limit|WidthOrHeightExceedsLimit'; then
      result=limit
    else
      result=other
    fi
  fi
  printf 'probe %s %s\n' "$name" "$result"
}

# Formats the policy refuses, then the same reads with the override allowing
# them.
probe msl magick identify "msl:$F/probe.msl"
probe mvg magick identify "mvg:$F/probe.mvg"
probe mvg-mixed-case magick identify "Mvg:$F/probe.mvg"
probe cut magick identify "cut:$F/probe.cut"
probe text magick identify "text:$F/probe.text"
probe txt magick identify "txt:$F/probe.txt"
probe ps magick identify "ps:$F/probe.ps"
probe ps-extension magick identify "$F/probe.ps"
probe ps-named-png magick identify "$F/postscript.png"
probe xc magick identify xc:red
probe https magick identify https://127.0.0.1/image.png
probe file-list magick identify "@$F/list.txt"
probe svg magick identify "svg:$F/probe.svg"
probe msvg magick identify "msvg:$F/probe.svg"
probe perl-mvg perl -MImage::Magick -e 'my $i=Image::Magick->new; my $e=$i->Read($ARGV[0]); die "$e\n" if $e; die "no image\n" unless @$i;' "mvg:$F/probe.mvg"
probe perl-ps-named-png perl -MImage::Magick -e 'my $i=Image::Magick->new; my $e=$i->Read($ARGV[0]); die "$e\n" if $e; die "no image\n" unless @$i;' "$F/postscript.png"
for case in msl mvg cut text txt ps; do
  file="$F/probe.$case"
  probe "control-$case" env HOME="$F/override" magick identify "$case:$file"
done
probe control-ps-named-png env HOME="$F/override" magick identify "$F/postscript.png"
# A png-named file holding MVG commands is never decoded as MVG without an
# explicit prefix, even where the MVG coder is allowed.
probe control-mvg-named-png env HOME="$F/override" magick identify "mvg:$F/mvg-probe.png"
probe mvg-named-png env HOME="$F/override" magick identify "$F/mvg-probe.png"
probe perl-mvg-named-png-undecoded perl -MImage::Magick -e 'my $i=Image::Magick->new; $i->Read($ARGV[0]); die "decoded\n" if @$i;' "$F/mvg-probe.png"

# Size limits and their boundary controls.
probe edge-width magick "$F/edge.png" "$F/edge-out.png"
probe over-width magick "$F/wide.png" "$F/wide-out.png"
probe over-height magick "$F/tall.png" "$F/tall-out.png"
probe over-width-eps magick "$F/huge.eps" "$F/huge-out.png"

# The reads and writes LaTeXML needs, outside LaTeXML.
probe png-to-png magick "$F/control.png" "$F/out.png"
probe png-to-jpeg magick "$F/control.png" "$F/out.jpg"
probe jpeg-to-png magick "$F/out.jpg" "$F/out-2.png"
probe cmyk-eps-to-png magick "$F/cmyk.eps" "$F/eps.png"
probe cmyk-pdf-to-png magick "$F/cmyk.pdf" "$F/pdf.png"
probe write-gif magick "$F/control.png" "$F/out.gif"
probe write-pdf magick "$F/control.png" "$F/out.pdf"
probe perl-png-jpeg perl -MImage::Magick -e 'for my $x (qw(png jpg)) { my $i=Image::Magick->new; my $e=$i->Read($ARGV[0]); die "$e\n" if $e; $e=$i->Write(filename=>"$ARGV[1].$x"); die "$e\n" if $e; my $r=Image::Magick->new; $e=$r->Read("$ARGV[1].$x"); die "$e\n" if $e; die "bad readback\n" unless $r->Get("width")==8 && $r->Get("height")==6; }' "$F/control.png" "$F/perl-control"

# Ghostscript refuses file access by default; -dNOSAFER is the control.
probe gs-default-file-read gs -q -dNODISPLAY -dBATCH -dNOPAUSE -c '(/etc/hostname) (r) file pop'
probe gs-nosafer-file-read gs -q -dNODISPLAY -dBATCH -dNOPAUSE -dNOSAFER -c '(/etc/hostname) (r) file pop'

# Record every Ghostscript command line ImageMagick runs during LaTeXML.
cat > "$F/bin/gs" <<SH
#!/bin/sh
log=\$(mktemp "$F/gs-argv.XXXXXX")
printf '%s\n' "\$@" > "\$log"
exec /usr/bin/gs "\$@"
SH
chmod 700 "$F/bin/gs"

latexml_run() {
  directory=$1
  home=$2
  (
    cd "$directory" || exit 1
    latexml --quiet --dest=doc.xml doc.tex >/dev/null 2>latexml.err || exit 1
    HOME="$home" PATH="$F/bin:$PATH" latexmlpost --dest=doc.html --format=html5 doc.xml >/dev/null 2>post.err
  )
}

D=/usr/share/texlive/texmf-dist/tex/latex/mwe
W="$F/w/allowed"
mkdir -p "$W"
cp "$D/example-image-a.eps" "$W/fig-eps.eps"
cp "$D/example-image-a.pdf" "$W/fig-pdf.pdf"
cp "$D/example-image-a.png" "$W/fig-png.png"
cp "$D/example-image-a.jpg" "$W/fig-jpg.jpg"
cp "$F/cmyk.eps" "$W/cmyk-eps.eps"
cp "$F/cmyk.pdf" "$W/cmyk-pdf.pdf"
cat > "$W/doc.tex" <<'TEX'
\documentclass{article}
\usepackage{graphicx}
\begin{document}
\includegraphics{fig-eps.eps}
\includegraphics{fig-pdf.pdf}
\includegraphics{fig-png.png}
\includegraphics{fig-jpg.jpg}
\includegraphics[angle=90]{fig-png.png}
\includegraphics[angle=90]{fig-jpg.jpg}
\includegraphics[width=2cm]{fig-eps.eps}
\includegraphics{cmyk-eps.eps}
\includegraphics{cmyk-pdf.pdf}
\end{document}
TEX
latexml_run "$W" "$HOME"
printf 'latexml allowed exit %s errors %s\n' "$?" "$(grep -c '^Error:' "$W/post.err")"
for src in $(grep -o '<img src="[^"]*"' "$W/doc.html" | sed 's/^<img src="//; s/"$//' | grep -v '^data:'); do
  printf 'graphic %s %s\n' "$src" "$(cd "$W" && magick identify -format '%m %wx%h' "$src" 2>/dev/null | head -n 1)"
done
for log in "$F"/gs-argv.*; do
  [ -f "$log" ] || continue
  printf 'gs %s\n' "$(grep -E '^-(dSAFER|dNOSAFER|dBATCH|dNOPAUSE|sDEVICE=.*)$' "$log" | tr '\n' ' ')"
  rm -f "$log"
done

# A PostScript program in a file named .png is refused inside LaTeXML, and
# Ghostscript never runs. The override renders it, which shows the refusal is
# the policy's.
for mode in refused control; do
  W="$F/w/$mode"
  mkdir -p "$W"
  cp "$F/postscript.png" "$W/figure.png"
  printf '%s\n' '\documentclass{article}\usepackage{graphicx}\begin{document}\includegraphics[angle=90]{figure.png}\end{document}' > "$W/doc.tex"
  if [ "$mode" = control ]; then home="$F/override"; else home="$HOME"; fi
  latexml_run "$W" "$home"
  calls=$(ls "$F"/gs-argv.* 2>/dev/null | wc -l | tr -d ' ')
  rm -f "$F"/gs-argv.*
  denied=$(grep -c "not allowed by the security policy \`PS'" "$W/post.err")
  images=$(grep -o '<img src="x[0-9]*\.png"' "$W/doc.html" | wc -l | tr -d ' ')
  printf 'latexml ps-named-png-%s policy-refusals %s gs-calls %s images %s\n' "$mode" "$denied" "$calls" "$images"
done

printf 'policy-paths %s\n' "$(magick -list policy | sed -n 's/^Path: //p' | tr '\n' ' ')"
echo imagemagick-policy-probe-complete
