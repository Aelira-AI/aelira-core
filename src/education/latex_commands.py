"""Names of LaTeX commands, for the checks that must not mistake LaTeX for text.

Two checks need to know whether a run of letters after a backslash is a LaTeX
command. ``math_speech`` reads a backslash command in a generated description as
mathematical markup, except at the start of a clearly identified Windows path,
and ``model_json`` recognises a command that JSON decoding swallowed into a
control character: ``\\theta`` written with one backslash arrives as a tab and
``heta``. Both use this one list so that they agree.

The list is broad on purpose and is case sensitive, because ``\\Rightarrow`` and
``\\rightarrow`` are different commands. It covers every spoken-form command of
``math_speech``, the amsmath and amssymb symbols, relations, negations and
arrows, delimiters, accents, font and spacing commands, and the commands a
description of a document or a code screenshot is likely to quote. A name that is
missing here is treated as ordinary text, so a swallowed command that is not
listed is not repaired. A name that is listed but is also an everyday word, such
as ``to`` or ``not``, only matters where a line break or a tab is followed by the
rest of that name, which is refused rather than guessed (see ``model_json``).
"""

from __future__ import annotations

_GREEK_AND_LETTERLIKE = """
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa
varkappa lambda mu nu xi omicron pi varpi rho varrho sigma varsigma tau upsilon
phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi
Omega varGamma varDelta varTheta varLambda varXi varPi varSigma varUpsilon
varPhi varPsi varOmega digamma aleph beth gimel daleth ell hbar hslash imath
jmath wp Re Im infty emptyset varnothing partial nabla angle measuredangle
sphericalangle surd top bot flat natural sharp clubsuit diamondsuit heartsuit
spadesuit square blacksquare triangle blacktriangle blacktriangledown lozenge
blacklozenge bigstar checkmark maltese Finv Game mho eth backepsilon complement
therefore because
"""

_OPERATORS = """
pm mp times div cdot ast star circ bullet cap cup uplus sqcap sqcup vee wedge
setminus wr diamond bigtriangleup bigtriangledown triangleleft triangleright
lhd rhd unlhd unrhd oplus ominus otimes oslash odot bigcirc dagger ddagger amalg
barwedge veebar doublebarwedge boxdot boxminus boxplus boxtimes divideontimes
dotplus smallsetminus curlyvee curlywedge intercal circledast circledcirc
circleddash centerdot leftthreetimes rightthreetimes ltimes rtimes Cap Cup
doublecap doublecup lor land lnot neg
"""

_RELATIONS = """
leq le geq ge neq ne equiv approx sim simeq cong asymp doteq propto models perp
parallel mid ll gg prec succ preceq succeq subset supset subseteq supseteq
sqsubset sqsupset sqsubseteq sqsupseteq in ni notin owns vdash dashv bowtie smile
frown leqq geqq leqslant geqslant eqslantless eqslantgtr lesssim gtrsim
lessapprox gtrapprox approxeq lessdot gtrdot lll llless ggg gggtr lessgtr
gtrless lesseqgtr gtreqless lesseqqgtr gtreqqless doteqdot Doteq fallingdotseq
risingdotseq backsim backsimeq subseteqq supseteqq Subset Supset curlyeqprec
curlyeqsucc precsim succsim precapprox succapprox vartriangleleft
vartriangleright trianglelefteq trianglerighteq triangledown blacktriangleleft
blacktriangleright between pitchfork smallsmile smallfrown shortmid shortparallel
bumpeq Bumpeq eqcirc circeq triangleq thicksim thickapprox varpropto vDash Vdash
Vvdash subsetneq supsetneq subsetneqq supsetneqq varsubsetneq varsupsetneq
varsubsetneqq varsupsetneqq exists forall
"""

# The negations, with the leading n that a swallowed ``\n`` takes with it.
_NEGATIONS = """
not nless ngtr nleq ngeq nleqq ngeqq nleqslant ngeqslant nsim ncong nmid
nparallel nprec nsucc npreceq nsucceq nsubset nsupset nsubseteq nsupseteq
nsubseteqq nsupseteqq nexists nvdash nvDash nVdash nVDash nequiv napprox
nshortmid nshortparallel ntriangleleft ntriangleright ntrianglelefteq
ntrianglerighteq nless ngtr nleftarrow nrightarrow nleftrightarrow nLeftarrow
nRightarrow nLeftrightarrow nearrow nwarrow nlessgtr ngtrless ngtrsim nlesssim
nprecsim nsimeq nsuccsim
"""

_ARROWS = """
leftarrow rightarrow leftrightarrow Leftarrow Rightarrow Leftrightarrow
longleftarrow longrightarrow longleftrightarrow Longleftarrow Longrightarrow
Longleftrightarrow mapsto longmapsto hookleftarrow hookrightarrow leftharpoonup
leftharpoondown rightharpoonup rightharpoondown rightleftharpoons
leftrightharpoons uparrow downarrow updownarrow Uparrow Downarrow Updownarrow
searrow swarrow leadsto to gets iff implies impliedby leftleftarrows
rightrightarrows leftrightarrows rightleftarrows Lleftarrow Rrightarrow
twoheadleftarrow twoheadrightarrow leftarrowtail rightarrowtail looparrowleft
looparrowright curvearrowleft curvearrowright circlearrowleft circlearrowright
Lsh Rsh upuparrows downdownarrows upharpoonleft upharpoonright downharpoonleft
downharpoonright multimap leftrightsquigarrow rightsquigarrow dashleftarrow
dashrightarrow restriction
"""

_DELIMITERS = """
left right middle big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr Biggl Biggr
lbrace rbrace lbrack rbrack langle rangle lceil rceil lfloor rfloor lvert rvert
lVert rVert vert Vert backslash lmoustache rmoustache lgroup rgroup ulcorner
urcorner llcorner lrcorner
"""

_LARGE_OPERATORS_AND_FUNCTIONS = """
sum prod coprod int iint iiint oint bigcup bigcap bigvee bigwedge bigoplus
bigotimes bigodot bigsqcup biguplus lim limsup liminf max min sup inf arg deg det
dim exp gcd hom ker lg ln log Pr sec csc sin cos tan cot sinh cosh tanh coth
arcsin arccos arctan mod bmod pmod pod ldots cdots vdots ddots dots dotsb dotsc
dotsi dotsm dotso
"""

_STRUCTURES_AND_ACCENTS = """
frac dfrac tfrac cfrac binom dbinom tbinom genfrac sqrt root over atop choose
brace brack stackrel overset underset overbrace underbrace overline underline
overleftarrow overrightarrow widehat widetilde hat check tilde acute grave dot
ddot dddot ddddot breve bar vec mathring substack sideset cancel bcancel xcancel
cancelto prime backprime
"""

_FONTS_STYLE_AND_SPACING = """
text textrm textbf textit textsf texttt textnormal textsc textup textsl textmd
textstyle textcolor textbackslash textasciitilde textasciicircum textunderscore
textwidth mathrm mathbf mathit mathsf mathtt mathcal mathscr mathfrak mathbb
mathnormal mathstrut mathbin mathrel mathop mathord mathopen mathclose
mathpunct boldsymbol bm pmb rm bf it sf tt cal scr frak Bbb rmfamily bfseries
itshape sffamily ttfamily displaystyle scriptstyle scriptscriptstyle limits
nolimits nonumber notag tag eqref ref label cite footnote footnotesize hspace
vspace quad qquad thinspace medspace thickspace negthinspace negmedspace
negthickspace hfill vfill phantom hphantom vphantom smash strut rlap mbox hbox vbox
fbox framebox raisebox makebox parbox rule color colorbox fcolorbox tiny
scriptsize small normalsize large Large LARGE huge Huge
"""

_DOCUMENT = """
begin end item newline newpage newcommand renewcommand newenvironment
renewenvironment newtheorem def let edef gdef xdef relax noindent indent bigskip
medskip smallskip centering raggedright raggedleft sloppy fussy documentclass
usepackage section subsection chapter title author date maketitle
includegraphics caption include input bibliography bibitem tableofcontents
tabular theorem frenchspacing nopagebreak nolinebreak nobreak normalfont
numberwithin
"""

LATEX_COMMAND_NAMES = frozenset(
    (
        _GREEK_AND_LETTERLIKE
        + _OPERATORS
        + _RELATIONS
        + _NEGATIONS
        + _ARROWS
        + _DELIMITERS
        + _LARGE_OPERATORS_AND_FUNCTIONS
        + _STRUCTURES_AND_ACCENTS
        + _FONTS_STYLE_AND_SPACING
        + _DOCUMENT
    ).split()
)

__all__ = ["LATEX_COMMAND_NAMES"]
