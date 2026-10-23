#!/usr/bin/perl
# Extract one function from the patched source for a sanitizer control.
#
# Usage: control-source.pl SOURCE START END GUARD_FILE OUTDIR
# START and END are literal text ("\n" means a newline). The function runs
# from START to the first END after it. GUARD_FILE holds the upstream fix's
# added text, which must occur exactly once in the function. OUTDIR receives
# patched.c (the exact function) and baseline.c (the function without the
# guard, which reproduces the unfixed code).
use strict;
use warnings;

my ($source, $start, $end, $guard_file, $out) = @ARGV;
die "usage\n" unless defined $out;
s/\\n/\n/g for $start, $end;
local $/;
open my $in, '<', $source or die "$source: $!\n";
my $text = <$in>;
open my $g, '<', $guard_file or die "$guard_file: $!\n";
my $guard = <$g>;
my $from = index($text, $start);
die "start marker not found once\n" if $from < 0 || index($text, $start, $from + 1) >= 0;
my $to = index($text, $end, $from);
die "end marker not found\n" if $to < 0;
my $function = substr($text, $from, $to + length($end) - $from);
my $count = () = $function =~ /\Q$guard\E/g;
die "guard occurs $count times in the function\n" unless $count == 1;
(my $baseline = $function) =~ s/\Q$guard\E//;
for (['patched.c', $function], ['baseline.c', $baseline]) {
    open my $fh, '>', "$out/$_->[0]" or die "$out/$_->[0]: $!\n";
    print $fh $_->[1];
}
