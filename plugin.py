# -*- coding: utf-8 -*-
###
# Copyright (c) 2012-2014, spline
# All rights reserved.
###
# my libs.
from BeautifulSoup import BeautifulSoup
from base64 import b64decode
import re
import collections
from itertools import groupby, count
from operator import itemgetter
import datetime
import json
import sqlite3  # db.
import os.path  # db.
import unicodedata
import jellyfish  # matching.
from metaphone import doublemetaphone  # matching.
# supybot libs
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks


class NFL(callbacks.Plugin):
    """Add the help for "@plugin help NFL" here
    This should describe *how* to use this plugin."""
    threaded = True

    def __init__(self, irc):
        self.__parent = super(NFL, self)
        self.__parent.__init__(irc)
        self._nfldb = os.path.abspath(os.path.dirname(__file__)) + '/db/nfl.db'
        self._playersdb = os.path.abspath(os.path.dirname(__file__)) + '/db/nfl_players.db'

    def die(self):
        self.__parent.die()

    ##############
    # FORMATTING #
    ##############

    def _red(self, string):
        """Returns a red string."""
        return ircutils.mircColor(string, 'red')

    def _yellow(self, string):
        """Returns a yellow string."""
        return ircutils.mircColor(string, 'yellow')

    def _green(self, string):
        """Returns a green string."""
        return ircutils.mircColor(string, 'green')

    def _blue(self, string):
        """Returns a blue string."""
        return ircutils.mircColor(string, 'blue')

    def _bold(self, string):
        """Returns a bold string."""
        return ircutils.bold(string)

    def _ul(self, string):
        """Returns an underline string."""
        return ircutils.underline(string)

    def _bu(self, string):
        """Returns a bold/underline string."""
        return ircutils.bold(ircutils.underline(string))

    ######################
    # INTERNAL FUNCTIONS #
    ######################

    def _splicegen(self, maxchars, stringlist):
        """Return a group of splices from a list based on the maxchars
        string-length boundary.
        """

        runningcount = 0
        tmpslice = []
        for i, item in enumerate(stringlist):
            runningcount += len(item)
            if runningcount <= int(maxchars):
                tmpslice.append(i)
            else:
                yield tmpslice
                tmpslice = [i]
                runningcount = len(item)
        yield(tmpslice)

    def _batch(self, iterable, size):
        """http://code.activestate.com/recipes/303279/#c7"""

        c = count()
        for k, g in groupby(iterable, lambda x:c.next()//size):
            yield g

    def _dtFormat(self, outfmt, instring, infmt):
        """Convert from one dateformat to another."""

        try:  # infmt/outfmt = "%m/%d/%Y"
            d = datetime.datetime.strptime(str(instring), infmt)
            return d.strftime(outfmt)
        except:
            return instring

    def _validate(self, date, format):
        """Return true or false for valid date based on format."""

        try:
            datetime.datetime.strptime(str(date), format)
            return True
        except ValueError:
            return False

    def _httpget(self, url, h=None, d=None, l=True):
        """General HTTP resource fetcher. Pass headers via h, data via d, and to log via l."""

        if self.registryValue('logURLs') and l:
            self.log.info(url)

        try:
            if h and d:
                page = utils.web.getUrl(url, headers=h, data=d)
            else:
                if not h:
                    h = {"User-Agent":"Mozilla/5.0 (X11; Ubuntu; Linux i686; rv:17.0) Gecko/20100101 Firefox/17.0"}
                else:
                    if h.get("User-Agent") is None:
                        h["User-Agent"] = "Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/33.0.1750.154 Safari/537.36"
                page = utils.web.getUrl(url, headers=h)
            return page
        except utils.web.Error as e:
            self.log.error("ERROR opening {0} message: {1}".format(url, e))
            return None

    def _remove_accents(self, data):
        """Unicode normalize for news."""

        nkfd_form = unicodedata.normalize('NFKD', unicode(data))
        return u"".join([c for c in nkfd_form if not unicodedata.combining(c)])

    def _b64decode(self, string):
        """Returns a base64 decoded string."""

        return b64decode(string)

    def _int_to_roman(self, i):
        """Returns a string containing the roman numeral from a number. For nflsuperbowl."""

        numeral_map = zip((1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1),
            ('M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I'))
        result = []
        for integer, numeral in numeral_map:
            count = int(i / integer)
            result.append(numeral * count)
            i -= integer * count
        return ''.join(result)

    def _millify(self, num):
        """Turns a number like 1,000,000 into 1M."""

        for unit in ['','k','M','B','T']:
            if num < 1000.0:
                return "%3.3f%s" % (num, unit)
            num /= 1000.0

    def _shortenUrl(self, url):
        """Shortens a long URL into a short one."""

        try:
            posturi = "https://www.googleapis.com/urlshortener/v1/url"
            data = json.dumps({'longUrl' : url})
            request = self._httpget(posturi, h={'Content-Type':'application/json'}, d=data, l=False)
            return json.loads(request)['id']
        except:
            return url

    ####################################
    # INTERNAL TEAM DATABASE FUNCTIONS #
    ####################################

    def _allteams(self, conf=None, div=None):
        """Return a string of all valid teams (abbr)."""

        with sqlite3.connect(self._nfldb) as conn:
            cursor = conn.cursor()
            if conf and not div:
                cursor.execute("SELECT team FROM nfl WHERE conf=?", (conf,))
            elif conf and div:
                cursor.execute("SELECT team FROM nfl WHERE conf=? AND div=?", (conf, div,))
            else:
                cursor.execute("SELECT team FROM nfl")
        # join all into list.
        teamlist = [item[0] for item in cursor.fetchall()]
        # return.
        return " | ".join(sorted(teamlist))

    def _validteams(self, optteam):
        """Takes optteam as input function and sees if it is a valid team.
        Aliases are supported via nflteamaliases table.
        Returns None upon error (no team name nor alias found.)
        Returns the team's 3-letter (ex: NE or ARI) if successful."""

        # first, set default value.
        returnval = None
        # now, do our sql.
        with sqlite3.connect(self._nfldb) as conn:
            cursor = conn.cursor()  # we only do exact matching here. no fuzzy.
            cursor.execute("SELECT team FROM nfl WHERE team=?", (optteam.upper(),))
            teamrow = cursor.fetchone()
            if teamrow:  # we found a team.
                returnval = str(teamrow[0])
            else:  # no team alias so we go back to normal team matching.
                query = "SELECT team FROM nflteamaliases WHERE teamalias LIKE ?"  # check aliases second.
                cursor.execute(query, ('%'+self._sanitizeName(optteam)+'%',))
                aliasrow = cursor.fetchone()
                if aliasrow:  # found team in regular nfl teams. no match? None.
                    returnval = str(aliasrow[0])
        # return time.
        return returnval

    def _translateTeam(self, db, column, optteam):
        """Translates optteam (validated via _validteams) into proper string using database column."""

        with sqlite3.connect(self._nfldb) as conn:
            cursor = conn.cursor()
            query = "SELECT %s FROM nfl WHERE %s='%s'" % (db, column, optteam)
            cursor.execute(query)  # obv, breaks if string is not there.
            row = cursor.fetchone()  # we want this so we can fix the function/code.
            return (str(row[0])) # return.

    ######################################
    # INTERNAL PLAYER DATABASE FUNCTIONS #
    ######################################

    def _sanitizeName(self, name):
        """ Sanitize name. """

        name = name.lower()  # lower.
        name = name.strip('.')  # remove periods.
        name = name.strip('-')  # remove dashes.
        name = name.strip("'")  # remove apostrophies.
        # possibly strip jr/sr/III suffixes in here?
        return name

    def _similarPlayers(self, optname):
        """Return a dict containing the five most similar players based on optname."""

        optname = self._sanitizeName(optname)  # first sanitize input to compare.
        jaro, damerau = [], []  # empty lists to put our results in.
        # now do our sql work.
        with sqlite3.connect(self._playersdb) as db:
            cursor = db.cursor()  # select all fullnames, eid, rid.
            cursor.execute("SELECT fullname, eid, rid FROM players")
            rows = cursor.fetchall()
        # close. iterate over all rows and do math.
        for row in rows:  # row[0] = fullname, row[1] = eid, row[2] = rid
            jaroscore = jellyfish.jaro_distance(optname, row[0])  # jaro.
            damerauscore = jellyfish.damerau_levenshtein_distance(optname, row[0])  #dld
            jaro.append({'jaro':jaroscore, 'fullname':row[0], 'eid':row[1], 'rid':row[2]})  # add dict to list.
            damerau.append({'damerau':damerauscore, 'fullname':row[0], 'eid':row[1], 'rid':row[2]})  # ibid.
        # now, we do two "sorts" to find the "top5" matches. reverse is opposite on each.
        jarolist = sorted(jaro, key=itemgetter('jaro'), reverse=True)[0:5]  # bot five.
        dameraulist = sorted(damerau, key=itemgetter('damerau'), reverse=False)[0:5]  # top five.
        # we now have two lists, top5 sorted, and need to do some further things.
        # now, lets iterate through both lists. match if both are in it. (better matches)
        matching = [k for k in jarolist if k['eid'] in [f['eid'] for f in dameraulist]]
        # now, test if we have anything. better matches will have more.
        if len(matching) == 0:  # we have NO matches. grab the top two from jaro/damerau (for error str)
            matching = [jarolist[0], dameraulist[0], jarolist[1], dameraulist[1]]
            self.log.info("NO MATCHES " + str(matching))
        # return matching now.
        return matching

    def _playerLookup(self, table, optname):
        """Return the specific id in column (eid, rid) for player."""

        optname = self._sanitizeName(optname)  # first sanitize.
        with sqlite3.connect(self._playersdb) as db:  # everything within 'with' cuz we might need to access db.
            cursor = db.cursor()  # first, check for an alias below.
            query = "SELECT %s FROM players WHERE eid IN (SELECT id FROM aliases WHERE name LIKE ?)" % (table)
            cursor.execute(query, ('%'+optname+'%',))  # wrap the alias in %.
            aliasrow = cursor.fetchone()
            if not aliasrow:  # if no alias.
                cursor = db.cursor()  # go into normal player db. %first%last% search.
                query = "SELECT %s FROM players WHERE fullname LIKE ?" % (table)
                cursor.execute(query, ('%'+optname.replace(' ', '%')+'%',))  # wrap in % and replace space with wc.
                row = cursor.fetchone()
                if not row:  # we did not find a %name%match% nor alias. check dm for mispellings.
                    namesplit = optname.split()  ############### clean-up function here. #####################
                    if len(namesplit) > 1:  # we have more than one, first and last. assume 0 is first, 1 is last.
                        fndm = doublemetaphone(namesplit[0])  # get our list of first-name dm.
                        lndm = doublemetaphone(namesplit[1])  # get our list of last-name dm.
                        if lndm[1] != '':  # if we have a secondary dm code.
                            query = "SELECT %s FROM players WHERE lndm1='%s' AND lndm2='%s'" % (table, lndm[0], lndm[1])
                        else:  # check only primary lastname dm.
                            query = "SELECT %s FROM players WHERE lndm1='%s'" % (table, lndm[0])
                        if fndm[1] != '': # likewise with first name.
                            query += " AND fndm1='%s' AND fndm2='%s'" % (fndm[0], fndm[1])
                        else:  # only check first name primary dm.
                            query += " AND fndm1='%s'" % (fndm[0])
                    else:  # assume one name given and that we check only on the last.
                        lndm = doublemetaphone(namesplit[0])
                        if lndm[1] != '':  # secondary dm code.
                            query = "SELECT %s FROM players WHERE lndm1='%s' AND lndm2='%s'" % (table, lndm[0], lndm[1])
                        else:  # primary dm check only.
                            query = "SELECT %s FROM players WHERE lndm1='%s'" % (table, lndm[0])
                    # now that we have DM query, execute.
                    cursor.execute(query)  # query constructed above.
                    row = cursor.fetchone()
                    if not row:  # dm failed. last chance to try using fuzzy string matching.
                        names = self._similarPlayers(optname)  # get a list of dicts back based on optname.
                        for sname in names:  # iterate through what we give back. might be different # of elements.
                            if 'jaro' in sname:  # don't know if we'll have jaro or damerau
                                if sname['jaro'] > 0.7:  # over the 0.7 threshold is usually good.
                                    optid = str(sname[table])  # grab the id we're looking for.
                                    break  # stop iteration.
                            if 'damerau' in sname:  # now if we have damerau. we're here if its a damerau match instead of jaro.
                                if sname['damerau'] < 7:  # less than seven on it.
                                    optid = str(sname[table])  # grab the id we're looking for.
                                    break  # break.
                        else:  # if we're here, we did NOT find any good jaro/damerau matches and out of the for loop.
                            optid = names  # we return a list of names. this is used to display "similar players"
                    else: # dm worked so we return the id matched by dm.
                        optid = str(row[0])
                else:  # fullname query worked so return the id matched by fullname.
                    optid = str(row[0])
            else:  # matched input via alias so we return that.
                optid = str(aliasrow[0])
        # close db and return the id.
        return optid

    #######################################
    # ALIAS AND PLAYER DB PUBLIC FUNCTION #
    #######################################

    def nfldb(self, irc, msg, args):
        """
        Return stats about the NFL teams and players databases.
        """

        # playerdb query.
        with sqlite3.connect(self._playersdb) as db:
            cursor = db.cursor()
            cursor.execute("SELECT Count() FROM players")
            numofplayers = cursor.fetchone()[0]
            cursor.execute("SELECT Count() FROM aliases")
            numofaliases = cursor.fetchone()[0]
        # teamdb query.
        with sqlite3.connect(self._nfldb) as db:
            cursor = db.cursor()
            cursor.execute("SELECT Count() FROM nfl")
            numofteams = cursor.fetchone()[0]
            cursor.execute("SELECT Count() FROM nflteamaliases")
            numofteamaliases = cursor.fetchone()[0]
        # print.
        irc.reply("NFLDB: I know about {0} NFL players, {1} player aliases, {2} teams and {3} team aliases.".format(\
            numofplayers, numofaliases, numofteams, numofteamaliases))

    nfldb = wrap(nfldb)

    ####################
    # PUBLIC FUNCTIONS #
    ####################

    def nflteams(self, irc, msg, args, optconf, optdiv):
        """[conference] [division]
        Display a list of NFL teams for input.
        Optional: use AFC or NFC for conference.
        It can also display specific divisions with North, South, East or West.
        Ex: nflteams or nflteams AFC or nflteams AFC East.
        """

        # first, check and lower our inputs if we have them.
        if optconf:
            optconf = optconf.lower()
        if optdiv:
            optdiv = optdiv.lower()
        # now decide what the query is based on input.
        if not optconf and not optdiv:  # all teams (no arguments)
            teams = self._allteams()
        elif optconf and not optdiv:  # just a conference (ex: NFC)
            if optconf == "afc" or optconf == "nfc":
                teams = self._allteams(conf=optconf)
            else:  # text must be afc or nfc.
                irc.reply("ERROR: Conference must be AFC or NFC")
                return
        elif optconf and optdiv:  # conf and div (ex: NFC North)
            if optconf == "afc" or optconf == "nfc":
                if optdiv == "north" or optdiv == "south" or optdiv == "east" or optdiv == "west":
                    teams = self._allteams(conf=optconf, div=optdiv)
                else:
                    irc.reply("ERROR: Division must be: North, South, East or West")
                    return
            else:
                irc.reply("ERROR: Conference must be AFC or NFC")
                return
        # output. conditional output.
        if optconf and not optdiv:  # just a conf (ex: NFC).
            output = "{0} teams ::".format(optconf.upper())
        elif optconf and optdiv:  # conf and div (ex: NFC North)
            output = "{0} {1} teams ::".format(optconf.upper(), optdiv.title())
        else:  # all teams.
            output = "NFL teams ::"
        # now the actual output.
        irc.reply("Valid {0} {1}".format(output, teams))

    nflteams = wrap(nflteams, [optional('somethingWithoutSpaces'), optional('somethingWithoutSpaces')])

    def nflhof(self, irc, msg, args, optyear):
        """[year]
        Display NFL Hall Of Fame inductees for year 1963 and on. Defaults to the latest year.
        Ex: 2010
        """

        if optyear:  # check for year or use the "last".
            testdate = self._validate(optyear, '%Y')
            if not testdate or int(optyear) < 1963:  # superbowl era and on.
                irc.reply("ERROR: Invalid year. Must be YYYY and after 1963.")
                return
        # build and process url.
        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS9ob2Yv')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'id':'hofers'})
        rows = table.findAll('tr', attrs={'class':''})
        # dict container for output.
        nflhof = collections.defaultdict(list)
        # each row on the page is HOF. insert with key as year, value is the player.
        for row in rows:
            num = row.find('td', attrs={'align':'right'})
            if num:
                tds = [item.getText() for item in row.findAll('td')]
                nflhof[int(tds[3])].append("{0} ({1})".format(tds[1], tds[2]))
        # if we don't have one specified, get the last year in the sort.
        if not optyear:
            optyear = nflhof.keys()[-1]
        # output time.
        output = nflhof.get(int(optyear), None)
        if not output:
            irc.reply("ERROR: Something broke looking up HOF class for: {0}".format(optyear))
            return
        else:
            irc.reply("{0} {1} :: {2}".format(self._bold(optyear), self._bold("NFL Hall of Fame class"), ' | '.join(output)))

    nflhof = wrap(nflhof, [optional('int')])

    def nflseasonsummary(self, irc, msg, args, optteam, optyear):
        """<TEAM> <YEAR>
        Display a team's schedule with win/loss from season.
        Ex: NE 2005 or GB 2010
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # test for valid year.
        if not self._validate(optyear, '%Y'):
            irc.reply("ERROR: '{0}' is an invalid year. Must input a valid year.".format(optyear))
            return
        # build and fetch url.
        lookupteam = self._translateTeam('pfrurl', 'team', optteam)
        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS90ZWFtcy8=') + '%s/%d.htm' % (lookupteam, optyear)
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        title = soup.find('h1', attrs={'class':'float_left'}).getText()  # team/season title.
        table = soup.find('table', attrs={'id':'team_gamelogs'})  # table.
        if not table:
            irc.reply("ERROR: I did not find a gamelog for {0} in {1}".format(optteam, optyear))
            return
        tbody = table.find('tbody')  # need the rows from here not elsewhere.
        rows = tbody.findAll('tr')  # so we can just find tr.
        # list container to put each game in.
        nflseason = []
        # each row is a game. there are two header rows we have conditionals for.
        for row in rows:
            tds = row.findAll('td')
            if len(tds) is not 21:  # make sure its a year played. crude but works for now.
                irc.reply("ERROR: I did not find a complete record for {0} in {1}. Season must be completed.".format(optteam, optyear))
                return
            week = tds[0].getText()
            if week.isdigit():  # If we're in a non-playoff week, prefix # with W.
                week = "W{0}".format(week)  # append W.
            date = tds[2].getText()
            if date == "Playoffs":  # skip this row.
                continue
            else:  # conv date (October 24 -> ##/##)
                date = self._dtFormat("%m/%d", date, "%B %d")
            result = tds[4].getText()  # W or L or T.
            vsat = tds[7].getText()   # @ or blank.
            if vsat != "@":  # if it's not @, we must add in vs.
                vsat = "vs."
            opp = tds[8].getText()
            if opp == "Bye Week":  # skip if "Bye Week"
                continue
            else:  # is opp. Shorten via translateTeam.
                try:  # try to shorten. we do this because older years have non-existent franchises.
                    opp = self._translateTeam('team', 'full', opp)
                except:  # pass if not found (keeps original team).
                    pass
            tmscore = tds[9].getText()
            oppscore = tds[10].getText()  # below, we finally append to the list.
            nflseason.append("{0} {1} {2} {3}{4} ({5}-{6})".format(self._red(week), date, result, self._ul(vsat), self._bold(opp), tmscore, oppscore))
        # output time.
        irc.reply("{0} :: {1}".format(self._blue(title), " | ".join(nflseason)))

    nflseasonsummary = wrap(nflseasonsummary, [('somethingWithoutSpaces'), ('int')])

    def nflawards(self, irc, msg, args, optyear):
        """<year>
        Display NFL Awards for a specific year. Use a year from 1966 on to the current year.
        Ex: 2003
        """

        testdate = self._validate(optyear, '%Y')
        if not testdate or int(optyear) < 1966:  # superbowl era and on.
            irc.reply("ERROR: Invalid year. Must be YYYY and after 1966.")
            return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS95ZWFycy8=') + '%s/' % optyear # 1966 on.
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process HTML.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        if not soup.find('h2', text="Award Winners"):
            irc.reply("ERROR: Could not find NFL Awards for the {0} season. Perhaps you are asking for the current season in-progress.".format(optyear))
            return

        table = soup.find('h2', text="Award Winners").findParent('div', attrs={'id':'awards'}).find('table')
        rows = table.findAll('tr')

        append_list = []

        for row in rows:
            award = row.find('td')
            player = award.findNext('td')
            append_list.append("{0}: {1}".format(self._bold(award.getText()), player.getText()))

        output = "{0} :: {1}".format(self._red(optyear + " NFL Awards"), " | ".join([item for item in append_list]))

        irc.reply(output)

    nflawards = wrap(nflawards, [('somethingWithoutSpaces')])

    def nflsuperbowl(self, irc, msg, args, optbowl):
        """<number|roman numeral|year>
        Display information from a specific Super Bowl.
        Ex: 39 or XXXIX or 2004.
        """

        if optbowl.isdigit():  # if fed digits, check if it's between 1966 and cur year..
            if not 1966 <= int(optbowl) <= datetime.datetime.now().year: # < 1966 is really what we need here.
                optbowl = self._int_to_roman(int(optbowl))  # convert to roman.
        # fetch url.
        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS9zdXBlci1ib3dsLw==')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'id':'superbowls'})
        rows = table.findAll('tr')[1:]  # first row is the header.
        # key/value dict we use for output.
        sb_data = collections.defaultdict(list)
        # one row per superbowl.
        for row in rows:
            tds = [item.getText() for item in row.findAll('td')]
            year = tds[0]  # year that we use as a key and part of the value.
            roman = re.sub('[^A-Z_]+', '', tds[1], re.UNICODE)  # clean up roman here.
            # value part is the appendString.
            appendString = "{0} Super Bowl {1} :: {2} {3} - {4} {5} :: MVP: {6} :: Location: {7} ({8}, {9})".format(\
                self._bold(year), self._red(roman), tds[2], tds[3], tds[4], tds[5], tds[6], tds[7], tds[8], tds[9])
            # append now. we double append because it's quick and cheap.
            sb_data[roman] = appendString
            sb_data[year] = appendString
        # output time.
        output = sb_data.get(optbowl.upper())
        if not output:
            irc.reply("ERROR: No Super Bowl found for: {0} (Check formatting)".format(optbowl))
        else:
            irc.reply(output)

    nflsuperbowl = wrap(nflsuperbowl, [('somethingWithoutSpaces')])

    def nflhead2head(self, irc, msg, args, optteam, optopp):
        """<team> <opp>
        Show all-time head-to-head records for regular season and playoffs of teams.
        Ex: NE NYJ
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        optopp = self._validteams(optopp)
        if not optopp: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # make sure they're not the same.
        if optteam == optopp:
            irc.reply("ERROR: Teams must be different from each other.")
            return
        # process and fetch url.
        lookupteam = self._translateTeam('pfrurl', 'team', optteam)
        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS90ZWFtcw==') + '/%s/head-to-head.htm' % lookupteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # work with html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'id':'head_to_head'}).find('tbody')
        rows = table.findAll('tr')[0:31]  # displays defunct so we limit by # of teams.
        # dict for output.
        head2head = collections.defaultdict(list)
        # each row is one of the 32
        for row in rows:
            tds = row.findAll('td')
            team = tds[0].find('a')['href'].split('/')[2]  # translateTeam pfrurl
            team = self._translateTeam('team', 'pfrurl', team)
            wins = tds[1].getText()
            loss = tds[2].getText()
            ties = tds[3].getText()
            # total = wins + loss + ties
            perc = tds[4].getText()
            pwins = tds[7].getText()
            ploss = tds[8].getText()
            # ptotal = pwins + ploss
            head2head[team] = ":: REG SEASON {0}-{1}-{2} ({3}) :: PLAYOFFS {4}-{5}".format(wins, loss, ties, perc, pwins, ploss)
        # output time.
        output = head2head.get(optopp)
        if not output:
            irc.reply("ERROR: For some reason, I have no head-to-head record between {0} and {1}".format(optteam, optopp))
            return
        else:
            irc.reply("{0} vs {1} {2}".format(self._red(optteam), self._red(optopp), output))

    nflhead2head = wrap(nflhead2head, [('somethingWithoutSpaces'), ('somethingWithoutSpaces')])

    def nflpracticereport (self, irc, msg, args, optteam):
        """<team>
        Display most recent practice report for team.
        Ex: NE.
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # process and fetch url.
        url = self._b64decode('aHR0cDovL2hvc3RlZC5zdGF0cy5jb20vZmIvcHJhY3RpY2UuYXNw')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # work with html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        # first, check if we have any practice reports. Offseason?
        if soup.find('div', attrs={'class':'warning'}, text="No practice report found."):
            irc.reply("ERROR: No practice reports found. Is it the offseason?")
            return
        timeStamp = soup.find('div', attrs={'id':'shsTimestamp'}).getText()
        tds = soup.findAll('td', attrs={'class':'shsRow0Col shsNamD', 'nowrap':'nowrap'})
        # defaultdict to put each report, one per team.
        practicereport = collections.defaultdict(list)

        for td in tds:
            team = td.findPrevious('h2', attrs={'class':'shsTableTitle'})
            team = self._translateTeam('team', 'full', team.getText())  # translate full team into abbr.
            player = td.find('a')
            appendString = "{0}".format(self._bold(player.getText()))
            report = td.findNext('td', attrs={'class':'shsRow0Col shsNamD'})
            if report:
                appendString += "({0})".format(report.getText())

            practicereport[team].append(appendString)
        # output time.
        output = practicereport.get(optteam, None)
        if output is None:  # some teams don't have practice reports.
            irc.reply("No recent practice reports for: {0} as of {1}".format(self._red(optteam), timeStamp.replace('Last updated ','')))
        else:
            irc.reply("{0} Practice Report ({1}) :: {2}".format(self._red(optteam), timeStamp, " | ".join(output)))

    nflpracticereport = wrap(nflpracticereport, [('somethingWithoutSpaces')])

    def nflteamdraft(self, irc, msg, args, optteam, optyear):
        """<team> <year>
        Display a team's draft picks from a specific year.
        Ex: NE 2010
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # check to make sure year is valid and between 1965 and now.
        testdate = self._validate(optyear, '%Y')
        if not testdate and (1965 > int(optyear) > datetime.datetime.now().year):
            irc.reply("ERROR: Invalid year. Must be YYYY and between 1965 and the current year.")
            return
        # build URL.
        url = self._b64decode('aHR0cDovL3d3dy5kcmFmdGhpc3RvcnkuY29tL2luZGV4LnBocC95ZWFycy8=') + '%s' % optyear
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'border':'1'})  # this is amb.
        firstrow = table.find('tr')  # our simple error check.
        h1 = firstrow.find('h1')
        if not h1:  # if draft is not available, like 2013 but in March, this will be None.
            irc.reply("ERROR: Draft for {0} is unavailable. Perhaps it has not occured yet?".format(optyear))
            return
        # if we do have h1, picks are from 3 and on due to header rows.
        rows = table.findAll('tr')[3:]
        # defaultdict(list) to put all picks in. key is the team. value = list of picks.
        teamdict = collections.defaultdict(list)
        # each row is a pick.
        for row in rows:
            tds = [item.getText() for item in row.findAll('td')]
            # translate the team here.
            pick_team = self._translateTeam('team', 'dh', tds[4].lower())
            # prep appendString.
            appendString = "{0}. {1} ({2} {3})".format(tds[2], tds[3], tds[5], tds[6])
            # add each pick key: team value: string
            teamdict.setdefault(pick_team, []).append(appendString)
        # output time.
        output = teamdict.get(optteam)  # optteam = key
        if not output:
            irc.reply("ERROR: I did not find any picks for {0} in {1}. Perhaps something broke?".format(optteam, optyear))
            return
        else:
            irc.reply("{0} draft picks in {1}({2}):: {3}".format(self._red(optteam),\
                    self._bold(optyear), len(output), " | ".join(output)))

    nflteamdraft = wrap(nflteamdraft, [('somethingWithoutSpaces'), ('int')])

    def nflweather(self, irc, msg, args, optteam):
        """<team>
        Display weather for the next game.
        Ex: NE
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # fetch url.
        url = self._b64decode('aHR0cDovL3d3dy5uZmx3ZWF0aGVyLmNvbS8=')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'class':'main'})
        tbody = table.find('tbody')
        rows = tbody.findAll('tr')
        # container for output.
        weatherList = collections.defaultdict(list)
        # each row is a game.
        for row in rows:
            tds = [item.getText() for item in row.findAll('td')]
            awayTeam = self._translateTeam('team', 'short', tds[0])  # translate into the team for each.
            homeTeam = self._translateTeam('team', 'short', tds[4])
            timeOrScore = tds[5]
            gameTemp = tds[8]
            appendString = "{0}@{1} - {2} - {3}".format(awayTeam, self._bold(homeTeam), timeOrScore, gameTemp)
            weatherList[awayTeam].append(appendString)
            weatherList[homeTeam].append(appendString)
        # output time.
        output = weatherList.get(optteam, None)
        if not output:
            irc.reply("ERROR: No weather found for: {0}. Team on bye?".format(optteam))
        else:
            irc.reply(" ".join(output))

    nflweather = wrap(nflweather, [('somethingWithoutSpaces')])

    def nfltrans(self, irc, msg, args):
        """
        Display latest NFL transactions.
        """

        # build and fetch url.
        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC90cmFuc2FjdGlvbnM=')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        div = soup.find('div', attrs={'id':'my-teams-table'})
        table = div.find('table', attrs={'class':'tablehead'})
        rows = table.findAll('tr', attrs={'class':re.compile('^oddrow team.*|^evenrow team.*')})

        transactions = []

        for row in rows[0:6]:
            transdate = row.findPrevious('tr', attrs={'class':'stathead'}).getText()
            transdate = self._dtFormat("%m/%d", transdate, "%A, %B %d, %Y")
            tds = row.findAll('td')
            team = tds[0].find('a')['href']
            team = team.split('/', 7)[-1].split('/')[0].upper()  # splits up url nicely.
            news = utils.str.ellipsisify(tds[1].getText(), 150)
            # append to transactions.
            transactions.append("{0} :: {1} :: {2}".format(transdate, self._red(team), news))

        for transaction in transactions:
            irc.reply(transaction)

    nfltrans = wrap(nfltrans)

    def nflprobowl(self, irc, msg, args, optyear):
        """<year>
        Display NFL Pro Bowlers for a year.
        Ex: 2011.
        """

        # must test the date.
        testdate = self._validate(optyear, '%Y')
        if not testdate and 1950 <= optyear <= datetime.datetime.now().year:
            irc.reply("ERROR: Invalid year. Must be YYYY. Year must also be between 1950 and current year.")
            return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL3d3dy5wcm8tZm9vdGJhbGwtcmVmZXJlbmNlLmNvbS95ZWFycw==') + '/%s/probowl.htm' % optyear
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        h1 = soup.find('h1')
        if not soup.find('table', attrs={'id':'pro_bowl'}):  # check to make sure we have probowlers.
            irc.reply("ERROR: I could not find any Pro Bowlers for {0}. Perhaps you specified this year where none have been selected yet?".format(optyear))
            return
        table = soup.find('table', attrs={'id':'pro_bowl'}).find('tbody')
        rows = table.findAll('tr', attrs={'class':''})
        # setup containers
        teams = {}  # container to count teams.
        positions = {}  # container to count positions.
        players = []  # put all in a container to output.
        # process each player.
        for row in rows:
            tds = [item.getText() for item in row.findAll('td')]
            pos = tds[0]
            player = tds[1]
            tm = tds[2]
            teams[tm] = teams.get(tm, 0) + 1 # team++.
            positions[pos] = positions.get(pos, 0) + 1 # positions++.
            players.append("{0}, {1} ({2})".format(self._bold(player), tm, pos)) # append player to list
        # we display the heading, total teams (len) and use teams, sorted in rev, top10.
        irc.reply("{0} :: Total Players: {1} - Total Teams: {2} - Top Teams: {3}".format(\
            self._red(h1.getText()), self._ul(len(players)), self._ul(len(teams)),\
            [k + ": " + str(v) for (k,v) in sorted(teams.items(), key=lambda x: x[1], reverse=True)[0:10]]))
        # now output players.
        irc.reply("{0}".format(" | ".join(players)))

    nflprobowl = wrap(nflprobowl, [('int')])

    def nflfines(self, irc, msg, args, optlist):
        """[--num #]
        Display latest NFL fines. Use --num # to display more than 3.
        Ex: --num 5
        """

        # handle optlist/optnumber
        optnumber = '5'
        if optlist:
            for (key, value) in optlist:
                if key == 'num':  # between 1 and 10, go to 5
                    if 1 <= value <= 10:
                        optnumber = '5'
                    else:
                        optnumber = value
        # fetch url.
        url = self._b64decode('aHR0cDovL3d3dy5qdXN0ZmluZXMuY29t')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html. little error checking.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES)
        heading = soup.find('div', attrs={'class':'title1'})
        div = soup.find('div', attrs={'class':'standing'})
        table = div.find('table')
        rows = table.findAll('tr', attrs={'class':'data'})
        # container for output.
        append_list = []
        # each row is a fine.
        for row in rows[0:int(optnumber)]:
            tds = [item.getText() for item in row.findAll('td')]
            # team = tds[2] # team is broken due to html comments
            append_list.append("{0} {1} {2} :: {3}".format(tds[0], self._bold(tds[3]), tds[4], tds[5]))

        for i, each in enumerate(append_list[0:int(optnumber)]):
            if i is 0:  # only for header row.
                irc.reply("Latest {0} :: Total {1} Fines.".format(heading.getText(), len(rows)))
                irc.reply(each)
            else:
                irc.reply(each)

    nflfines = wrap(nflfines, [getopts({'num':('int')})])

    def nflweeklyleaders(self, irc, msg, args):
        """
        Display weekly NFL Leaders in various categories.
        """

        # build and fetch url.
        url = self._b64decode('aHR0cDovL20uZXNwbi5nby5jb20vbmZsL2xlYWRlcnM/d2pi')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES)
        tables = soup.findAll('table', attrs={'class':'table'})
        subheading = soup.find('div', attrs={'class':'sub dark'})
        # container output.
        weeklyleaders = collections.defaultdict(list)
        # parse each table, which is a stat category.
        for table in tables:
            rows = table.findAll('tr')  # all rows, first one, below, is the heading
            heading = rows[0].find('td', attrs={'class':'sec row', 'width':'65%'})
            append_list = []  # container per list
            for i, row in enumerate(rows[1:]):  # rest of the rows, who are leaders.
                tds = row.findAll('td')
                player = tds[1]
                stat = tds[2]  # +1 the count so it looks normal, bold player/team and append.
                append_list.append("{0}. {1} ({2})".format(i+1, self._bold(player.getText()), stat.getText()))
            # one we have everything in the string, append, so we can move into the next category.
            weeklyleaders[heading.getText()] = append_list
        # output time.
        for i,x in weeklyleaders.items():
            irc.reply("{0} {1} :: {2}".format(self._red(i), self._red(subheading.getText()), " ".join(x)))

    nflweeklyleaders = wrap(nflweeklyleaders)

    def nfltopsalary(self, irc, msg, args, optlist, optposition):
        """[--average|--caphit] [position]
        Display various NFL player and team salary information.
        Use --average to display the highest average salary.
        Use --caphit to display highest cap-hit.
        Other option is: position. Use the command with an argument to display valid positions.
        """

        average, caphit = False, False
        for (option, arg) in optlist:
            if option == 'average':
                average, caphit = True, False
            if option == 'caphit':
                caphit, average = True, False

        positions = [   'center','guard','tackle','tight-end','wide-receiver','fullback',
                        'running-back', 'quarterback', 'defensive-end', 'defensive-tackle', 'linebacker',
                        'cornerback', 'safety', 'kicker', 'punter', 'kick-returner', 'long-snapper'
                    ]

        # construct url.
        url = self._b64decode('aHR0cDovL3d3dy5zcG90cmFjLmNvbS90b3Atc2FsYXJpZXM=') + '/nfl/'
        if average:
            url += 'average/'
        if caphit:
            url += 'cap-hit/'
        if optposition:
            if optposition not in positions:
                irc.reply("ERROR: Position not found. Must be one of: %s" % positions)
                return
            else:
                url += '%s/' % optposition
        # construct and fetch url.
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html.replace('\n',''))
        tbody = soup.find('tbody')
        rows = tbody.findAll('tr')[0:5] # just do top5 because some lists are long.

        append_list = []

        for row in rows:
            rank = row.find('td', attrs={'style':'width:20px;'}).find('center')
            #team = rank.findNext('td', attrs={'class':re.compile('logo.*?')}).find('img')['src'].replace('http://www.spotrac.com/assets/images/thumb/','').replace('.png','')
            # self._translateTeam('st', 'team', str(team))
            player = row.find('td', attrs={'class':re.compile('player .*?')}).find('a')
            # position = player.findNext('span', attrs={'class':'position'})
            salary = row.find('span', attrs={'class':'playersalary'}).getText().replace('$','').replace(',','')
            append_list.append("{0}. {1} {2}".format(rank.getText().strip(), self._bold(player.getText().strip()), self._millify(float(salary))))

        # make title
        title = self._red('NFL Top Salaries')
        # add to title, depending on what's going on
        if caphit:
            title += " (cap-hit)"
        if average:
            title += " (average salaries)"
        if optposition:
            title += " at %s" % (optposition)
        # now output
        irc.reply("{0}: {1}".format(title, " | ".join([item for item in append_list])))

    nfltopsalary = wrap(nfltopsalary, [(getopts({'average':'', 'caphit':''})), optional('somethingWithoutSpaces')])

    def nflleagueleaders(self, irc, msg, args, optlist, optcategory, optstat, optyear):
        """[--postseason] <category> <stat> [year]
        Display NFL statistical leaders in a specific category for a stat. Year, which can go back until 2001, is optional.
        Use --postseason to show post-season stats.
        Ex: Passing td or Punting punts 2003. Stats show regular season.
        """

        statsCategories = {
                'Passing': {
                    'qbr':'49',
                    'comp':'1',
                    'att':'2',
                    'comp%':'41',
                    'yards':'4',
                    'yards/gm':'42',
                    'td':'5',
                    'int':'3',
                    'sacked':'8',
                    'sackedyardslost':'9',
                    'fumbles':'47',
                    'fumbleslost':'48'
                },
                'Rushing': {
                    'rushes':'16',
                    'yards':'17',
                    'yards/g':'39',
                    'avg':'40',
                    'td':'18',
                    'fumbles':'47',
                    'fumbleslost':'48'
                },
                'Receiving': {
                    'receptions':'27',
                    'recyards':'28',
                    'yards/gm':'44',
                    'yards/avg':'45',
                    'longest':'30',
                    'yac':'46',
                    '1stdowns':'33',
                    'tds':'29',
                    'fumbles':'47',
                    'fumbleslost':'48'
                },
                'Kicking': {
                    '0-19':'208',
                    '20-29':'210',
                    '30-39':'212',
                    '40-49':'214',
                    '50+':'216',
                    'fgm':'222',
                    'fga':'221',
                    'pct':'230',
                    'longest':'224',
                    'xpm':'225',
                    'xpa':'226',
                    'xp%':'231'
                },
                'Returns':{
                    'kickoffreturns':'311',
                    'kickoffyards':'312',
                    'kickoffavg':'319',
                    'kickofflongest':'314',
                    'kickofftd':'315',
                    'puntreturns':'301',
                    'puntreturnyards':'302',
                    'puntreturnavg':'320',
                    'puntreturnlongest':'304',
                    'puntreturntds':'305'
                },
                'Punting': {
                    'punts':'402',
                    'puntyards':'403',
                    'puntavg':'411',
                    'puntlong':'408',
                    'puntwithin20':'404',
                    'puntwithin10':'405',
                    'faircatch':'401',
                    'touchback':'406',
                    'blocked':'407'
                },
                'Defense':{
                    'solotackles':'128',
                    'assistedtackles':'129',
                    'totaltackles':'130',
                    'sacks':'106',
                    'sacksyardslost':'107',
                    'stuffs':'101',
                    'stuffsyardslost':'102',
                    'int':'108',
                    'intyards':'109',
                    'inttds':'110',
                    'deftd':'103',
                    'forcedfumbles':'114',
                    'pd':'113',
                    'safety':'115'
                }
            }
        # must title this category
        optcategory = optcategory.title()
        if optcategory not in statsCategories:
            irc.reply("ERROR: Category must be one of: {0}".format(" | ".join(sorted(statsCategories.keys()))))
            return
        # category statkey is lower.
        optstat = optstat.lower()
        if optstat not in statsCategories[optcategory]:
            irc.reply("ERROR: Stat for {0} must be one of: {1}".format(optcategory, " | ".join(sorted(statsCategories[optcategory].keys()))))
            return
        # if we have a year.
        if optyear:
            testdate = self._validate(optyear, '%Y')
            if not testdate and int(optyear) < 2000:
                irc.reply("ERROR: Invalid year. Must be YYYY. Year must also be between 2001 and current year.")
                return
        # handle --optlist.
        postseason = False
        for (option, arg) in optlist:
            if option == 'postseason':
                postseason = True
        # build url and fetch.
        url = self._b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbmZsL3N0YXRzL2J5Y2F0ZWdvcnk=')
        url += '?cat=%s&conference=NFL&sort=%s&timeframe=All' % (optcategory, statsCategories[optcategory][optstat])
        if optyear:  # don't need year for most current.
            if not postseason:
                url += '&year=season_%s' % optyear
            else:
                url += '&year=postseason_%s' % optyear
        # build and fetch url.
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html.replace('&nbsp;',''))
        selectedyear = soup.find('select', attrs={'name':'year'}).find('option', attrs={'selected':'selected'})  # creative way to find the year.
        table = soup.find('tr', attrs={'class':'ysptblthmsts', 'align':'center'}).findParent('table')
        # header = table.findAll('tr')[1].findAll('td')
        rows = table.findAll('tr')[2:]  # start at 3 due to headers.
        # container we'll put all stats in.
        append_list = []
        # each row is a player, ranked in order.
        for row in rows:
            tds = [item for item in row.findAll('td')]
            sortfield = row.find('span', attrs={'class':'yspscores'})  # whatever field you are sorting by will have this span inside the td.
            append_list.append("{0} ({1}) - {2}".format(self._bold(tds[0].getText()), tds[1].getText(), sortfield.getText()))
        # output time.
        title = "Top in {0}({1}) for {2}".format(optcategory, optstat, selectedyear.getText())
        output = "{0} :: {1}".format(self._red(title), " | ".join([item for item in append_list]))
        irc.reply(output)

    nflleagueleaders = wrap(nflleagueleaders, [(getopts({'postseason':''})), ('somethingWithoutSpaces'), ('somethingWithoutSpaces'), optional('somethingWithoutSpaces')])

    def nflteamrankings(self, irc, msg, args, optteam):
        """<team>
        Display team rankings for off/def versus the rest of the NFL.
        Ex: NE
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC90ZWFtL18vbmFtZQ==') + '/%s/' % optteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        div = soup.find('div', attrs={'class':'mod-container mod-stat'})
        h3 = div.find('h3')
        statsfind = div.findAll('div', attrs={'class':re.compile('span-1.*?')})
        # container to throw it all in.
        append_list = []
        # each item we find to append.
        for stats in statsfind:
            header = stats.find('h4')
            stat = stats.find('span', attrs={'class':'stat'})
            rank = stat.findNext('strong')
            append_list.append("{0} {1} ({2})".format(self._bold(header.getText()), stat.getText(), rank.getText()))
        # output.
        descstring = " | ".join([item for item in append_list])
        irc.reply("{0} :: {1} :: {2}".format(self._red(optteam), self._ul(h3.getText()), descstring))

    nflteamrankings = wrap(nflteamrankings, [('somethingWithoutSpaces')])

    def nflstandings(self, irc, msg, args, optlist, optconf, optdiv):
        """[--detailed] [conf] [division]
        Display NFL standings for a division. Requires a conference and division.
        Use --detailed to display full table. Ex: AFC East
        """

        # handle optlist.
        detailed = False
        for (option, arg) in optlist:
            if option == 'detailed':
                detailed = True
        # we have to verify the optconf and optdiv.
        optconf = optconf.upper()
        optdiv = optdiv.upper()
        if optconf != "AFC" and optconf != "NFC":
            irc.reply("ERROR: Conference must be AFC or NFC.")
            return
        if optdiv != "NORTH" and optdiv != "SOUTH" and optdiv != "EAST" and optdiv != "WEST":
            irc.reply("ERROR: Division must be North, South, East or West.")
            return
        # lets fetch our standings.
        url = self._b64decode('aHR0cDovL3Nwb3J0cy1hay5lc3BuLmdvLmNvbS9uZmwvc3RhbmRpbmdz')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'class':'tablehead', 'cellspacing':'1', 'cellpadding':'3'})
        # sanity check.
        if not table:
            irc.reply("ERROR: I can't find  the NFL standings table. Something broke.")
            return
        # process the rows (teams) and setup containers.
        rows = table.findAll('tr', attrs={'class':re.compile('(^oddrow|^evenrow).*')})
        s = collections.defaultdict(list)  # container to put all the html data into.
        ll = collections.defaultdict(list)  # sep data structure to determine length.
        for row in rows:
            # find the colhead
            colhead = row.findPrevious('tr', attrs={'class':'colhead'})
            chcell = [i.getText() for i in colhead.findAll('td')]
            # NFC EAST, W, L, T, PCT, HOME, ROAD, DIV, CONF, PF, PA, DIFF, STRK
            div = chcell[0]  # first one is the division.
            t = {}
            for i, td in enumerate(row.findAll('td')):
                # iterate over the "rows" and add into s container.
                # we also add into lengthlist the lengths for output later.
                if i == 0:  # first row the colhead is DIV so we replace w/team.
                    t['TEAM'] = td.getText()
                    ll['TEAM'].append(len(td.getText()))
                else:  # anyting else we use the chcell.
                    t[chcell[i]] = td.getText()
                    ll[chcell[i]].append(len(td.getText()))
            # now add the tmp dict into the defaultdict
            s[div].append(t)
        # now that we're done, lets prep for output.
        out = optconf + " " + optdiv  # out = key for s like (AFC EAST)
        # now we do our actual output.
        if not detailed:  # short output one-liner.
            short = [self._bold(z['TEAM']) + " (" + z['W'] + "-" + z['L'] + "-" + z['T'] + ")" for z in s[out]]
            irc.reply("{0} :: {1}".format(self._red(out), " ".join(short)))
        else:  # detailed.
            # we have to do a static order. it can break but makes the code simpler.
            outorder = ['TEAM', 'W', 'L', 'T', 'PCT', 'HOME', 'ROAD', 'DIV', 'CONF', 'PF', 'PA', 'DIFF', 'STRK']
            # first, lets print the header row. easier on the code below.
            hrs = []
            # iterate over the header row list. we use the list entries as keys for lengthlist.
            for hr in outorder:
                if hr == "TEAM":  # first entry is normally TEAM. we replace this with the conf but maintain spacing.
                    hrs.append("{0:{1}}".format(out, max(ll[hr])+4, key=int))
                else:  # regular append with hr = key matching up in ll + 2 for spacing.
                    hrs.append("{0:{1}}".format(hr, max(ll[hr])+4, key=int))
            # output the header row. we join all entries in the list.
            irc.reply(" ".join(hrs))
            # now lets iterate over the keys of out, which are rows of each team.
            for o in s[out]:  # out = values in s that match our key (conf + div) and is verified above.
                l = []  # list container we populate with each line to display.
                for y in outorder:  # we iterate over the outorder list to populate l. the spacing is done via ll+2.
                    l.append("{0:<{1}}".format(o[y],  max(ll[y])+4, key=int))
                # now output one line at a time.  we join all entries in the list.
                irc.reply(" ".join(l))

    nflstandings = wrap(nflstandings, [getopts({'detailed':''}), ('somethingWithoutSpaces'), ('somethingWithoutSpaces')])

    def _format_cap(self, figure):
        """Format cap numbers for nflcap command."""

        figure = figure.replace(',', '').strip()  # remove commas.
        if figure.startswith('-'):  # figure out if we're a negative number.
            negative = True
            figure = figure.replace('-','')
        else:
            negative = False

        try:  # try and millify.
            figure = self._millify(float(figure))
        except:
            figure = figure

        if negative:
            figure = "-" + figure
        # now return
        return figure

    def nflcap(self, irc, msg, args, optteam):
        """<team>
        Display team's NFL cap situation.
        Ex: GB
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # need the specific spotrac for the url.
        lookupteam = self._translateTeam('spotrac', 'team', optteam)
        # fetch url.
        url = self._b64decode('aHR0cDovL3d3dy5zcG90cmFjLmNvbS9uZmwv') + '%s/cap-hit/' % lookupteam
        html = self._httpget(url)  #, h={"Content-type": "application/x-www-form-urlencoded"}, d={'ajax':'1'})
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        teamtitle = soup.find('title')
        basespan = soup.find('span', text="Cap Space")  # we derive the tbody via a specific span.
        tbody = basespan.findParent('tbody')  # find the proper tbody.
        ztrs = tbody.findAll('tr') # inside that tbody, we have to do some disgusting regex.
        # create the container to dump the shit we want in it.
        trs = []
        # have to change this -- again. disgusting but stops all the changing on the site.
        for ztr in ztrs:  # go over our row.
            ztds = ztr.findAll('td')  # in each row, look at the tds class.
            if ztds:  # make sure there are tds in the tr.
                for f in ztds:  # iterate over those tds now.
                    if f['class'] == "total team total-title":  # very specific td class we're looking for.
                        parent = f.findParent('tr')  # find the td's parent.
                        trs.append(parent)  # append the parent into our trs list container.
                        break  # break out of the tds so we don't inject multiple trs.
        # container for output.
        capfigs = []
        # now iterate over these.
        for tr in trs:
            tds = tr.findAll('td')  # find all td in tr.
            n = self._bold(tds[0].getText().encode('utf-8'))  # bold title.
            f = self._format_cap(tds[-1].getText().encode('utf-8'))  # format cap figure.
            capfigs.append("{0}: {1}".format(n, f))  # append to list.
        # now format output.
        output = "{0} :: {1}".format(self._red(teamtitle.getText()), " | ".join([i for i in capfigs]))
        irc.reply(output)

    nflcap = wrap(nflcap, [('somethingWithoutSpaces')])

    def nflcoachingstaff(self, irc, msg, args, optteam):
        """<team>
        Display a NFL team's coaching staff.
        Ex: NE
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL2VuLndpa2lwZWRpYS5vcmcvd2lraS9MaXN0X29mX2N1cnJlbnRfTmF0aW9uYWxfRm9vdGJhbGxfTGVhZ3VlX3N0YWZmcw==')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html from wiki.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        tables = soup.findAll('table', attrs={'style':'text-align: left;'})
        # container for output.
        coachingstaff = collections.defaultdict(list)
        # now process through each table and populate.
        for table in tables:
            listitems = table.findAll('li')[3:]
            for li in listitems:
                team = li.findPrevious('h3')
                team = self._translateTeam('team', 'full', team.getText())
                coachingstaff[team].append(li.getText().replace(u' –',': '))
        # output time.
        output = coachingstaff.get(str(optteam), None)
        if not output:
            irc.reply("ERROR: Failed to find coaching staff for: {0}. Maybe something broke?".format(optteam))
        else:
            irc.reply("{0} :: {1}".format(self._red(optteam), " | ".join([item.encode('utf-8') for item in output])))

    nflcoachingstaff = wrap(nflcoachingstaff, [('somethingWithoutSpaces')])

    def nflroster(self, irc, msg, args, optteam, optposition):
        """<team> <position/#>

        Display who on a team matches #jersey number or <position>.
        Ex: nflroster NE QB (all QBs on NE) or NFL NE 12 (NE roster #12)
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # setup the defaults.
        lookupteam = optteam
        useNum = True
        optposition = optposition.replace('#','') # remove # infront of # if there.
        if not optposition.isdigit(): # if we are not a digit, check if we're in valid positions.
            useNum = False
            optposition = optposition.upper()

        # build and fetch url
        url = 'http://espn.go.com/nfl/team/roster/_/name/%s/' % lookupteam
        # url = self._b64decode('') + '/%s' % lookupteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        # process html
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        div = soup.find('div', attrs={'id':'my-players-table'})
        # setup defaultdicts for output.
        nflroster = collections.defaultdict(list)
        positiongroups = collections.defaultdict(list)
        # find the rows.
        rows = div.findAll('tr', attrs={'class':re.compile('^oddrow.*?|^evenrow.*?')})
        for row in rows:
            tds = row.findAll('td')
            number = tds[0].getText()
            player = tds[1].getText()
            position = tds[2].getText()
            nflroster[number].append("{0} ({1})".format(player, position))
            positiongroups[position].append("#{0} {1}".format(number, player))
        # prepare output.
        if useNum:
            if optposition in nflroster:
                output = "{0} #{1} is: {2}".format(self._red(optteam), self._bold(optposition), "".join(nflroster.get(optposition)))
            else:
                output = "I did not find a person matching number: {0} on {1}".format(optposition, optteam)
        else:
            if optposition in positiongroups:
                output = "{0} on {1} :: {2}".format(self._bold(optposition), self._red(optteam), " | ".join(positiongroups.get(optposition)))
            else:
                output = "I did not find any at the position on {0} position :: Valid positions on {1}'s roster: {2}".format(optposition, optteam, " | ".join(sorted(positiongroups.keys())))

        irc.reply("{0}".format(output))

    nflroster = wrap(nflroster, [('somethingWithoutSpaces'), ('somethingWithoutSpaces')])

    def nflteamdraftpicks(self, irc, msg, args, optteam):
        """<team>
        Display total NFL draft picks for a team and what round.
        Ex: NE
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return

        url = self._b64decode('aHR0cDovL3d3dy5mZnRvb2xib3guY29tL25mbF9kcmFmdA==') + '/' + str(datetime.datetime.now().year) + '/nfl_draft_order_full.cfm'
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        if not soup.find('div', attrs={'id':'content_nosky'}):
            irc.reply("ERROR: Something broke on formatting.")
            return

        div = soup.find('div', attrs={'id':'content_nosky'})
        h1 = div.find('h1', attrs={'class':'newpagetitle'}).getText()
        table = div.find('table', attrs={'class':'fulldraftorder'})
        rows = table.findAll('tr')[1:]  # skip the first row.

        nflteampicks = collections.defaultdict(list)

        for row in rows:
            tds = row.findAll('td')
            team = tds[0].getText().strip().replace('WAS','WSH')  # again a hack for people using WAS instead of WSH.
            numofpicks = tds[1].getText().strip()
            pickrounds = tds[2].getText().strip()
            appendString = "({0}) Picks: {1}".format(numofpicks, pickrounds)
            nflteampicks[str(team)].append(appendString)

        # output time.
        output = nflteampicks.get(optteam, None)
        if not output:
            irc.reply("ERROR: Team not found. Something break?")
            return
        else:
            irc.reply("{0} :: {1} :: {2}".format(self._red(h1), self._bold(optteam), "".join(output)))

    nflteamdraftpicks = wrap(nflteamdraftpicks, [('somethingWithoutSpaces')])

    def nfldraftorder(self, irc, msg, args, optlist):
        """[--round #]
        Display current NFL Draft order for next year's draft.
        Will default to display the first round. Use --round # to display another (1-7)
        """

        optround = "1"  # by default, show round 1.

        # handle getopts.
        if optlist:
            for (key, value) in optlist:
                if key == 'round':
                    if 1 < value < 7:
                        irc.reply("ERROR: Round must be between 1-7")
                        return
                    else:
                        optround = value

        url = self._b64decode('aHR0cDovL3d3dy5mZnRvb2xib3guY29tL25mbF9kcmFmdA==') + '/' + str(datetime.datetime.now().year) + '/nfl_draft_order.cfm'
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        if not soup.find('div', attrs={'id':'content'}):
            irc.reply("ERROR: Something broke in formatting on the NFL Draft order page.")
            return

        # now process html
        div = soup.find('div', attrs={'id':'content'})
        h1 = div.find('h1', attrs={'class':'newpagetitle'}).getText()
        optround = "Round %s" % (optround)  # create "optround" total hack but works.
        round = div.find('h2', text=optround).findNext('ol')  # ol container, found by text.
        rows = round.findAll('li')  # each li has an a w/the team.

        append_list = []

        # go through each and append to list. This is ugly but it works.
        for i, row in enumerate(rows):
            rowtext = row.find('a')
            if rowtext:
                rowtext.extract()
                rowtext = rowtext.getText().strip().replace('New York','NY')  # ugly spaces + wrong NY.
                rowtext = self._translateTeam('team', 'draft', rowtext)  # shorten teams.

            # now, handle appending differently depending on what's left in row after extract()
            if len(row.getText().strip()) > 0:  # handle if row has more after (for a trade)
                append_list.append("{0}. {1} {2}".format(i+1,rowtext, row.getText().strip()))  # +1 since it starts at 0.
            else:  # most of the time, it'll be empty.
                append_list.append("{0}. {1}".format(i+1,rowtext))

        # now output
        irc.reply("{0}({1}) :: {2}".format(self._red(h1), self._bold(optround), " ".join(append_list)))

    nfldraftorder = wrap(nfldraftorder, [getopts({'round': ('int')})])

    def nflplayoffs(self, irc, msg, args):
        """
        Display the current NFL playoff match-ups if the season ended today.
        """

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9zdGFuZGluZ3MvXy90eXBlL3BsYXlvZmZzL3NvcnQvY29uZmVyZW5jZVJhbmsvb3JkZXIvZmFsc2U=')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        if not soup.find('table', attrs={'class':'tablehead', 'cellpadding':'3'}):
            irc.reply("Failed to find table for parsing.")
            return

        table = soup.find('table', attrs={'class':'tablehead', 'cellpadding':'3'})
        rows = table.findAll('tr', attrs={'class': re.compile('^oddrow.*?|^evenrow.*?')})

        nflplayoffs = collections.defaultdict(list)

        for row in rows:  # now build the list. table has rows with the order. we work with 1-6 below when outputting.
            conf = row.findPrevious('tr', attrs={'class':'stathead'}).find('td', attrs={'colspan':'13'})
            conf = str(conf.getText().replace('National Football Conference','NFC').replace('American Football Conference','AFC'))

            tds = row.findAll('td')  # now get td in each row for making into the list
            #rank = tds[0].getText()
            team = tds[1].getText().replace('z -', '').replace('y -', '').replace('x -', '').replace('* -','') # short.
            #self.log.info(str(team))
            #team = self._translateTeam('team', 'short', team)
            #reason = tds[10].getText()
            appendString = "{0}".format(self._bold(team.strip()))
            nflplayoffs[conf].append(appendString)

        for i, x in nflplayoffs.iteritems():
            matchups = "{6} :: BYES: {4} and {5} | WC: {3} @ {0} & {2} @ {1} | In the Hunt: {7} & {8}".format(\
                x[2], x[3], x[4], x[5], x[0], x[1], self._red(i), x[6], x[7])
            irc.reply(matchups)

    nflplayoffs = wrap(nflplayoffs)


    def nflteamtrans(self, irc, msg, args, optteam):
        """<team>
        Shows recent NFL transactions for team.
        Ex: CHI
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return

        lookupteam = self._translateTeam('eid', 'team', optteam)

        url = self._b64decode('aHR0cDovL20uZXNwbi5nby5jb20vbmZsL3RlYW10cmFuc2FjdGlvbnM=') + '?teamId=%s&wjb=' % lookupteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        t1 = soup.findAll('div', attrs={'class':re.compile('(^ind tL$|^ind alt$|^ind$)')})

        if len(t1) < 1:
            irc.reply("No transactions found for: {0}".format(optteam))
            return

        for item in t1:
            if "href=" not in str(item):
                trans = item.findAll(text=True)
                irc.reply("{0:8} {1}".format(self._bold(trans[0]), trans[1]))

    nflteamtrans = wrap(nflteamtrans, [('somethingWithoutSpaces')])

    def nflinjury(self, irc, msg, args, optlist, optteam):
        """[--details] <TEAM>
        Show all injuries for team.
        Use --details to display full table with team injuries.
        Ex: NE or --details NYG
        """

        # handle optlist input.
        details = False
        for (option, arg) in optlist:
            if option == 'details':
                details = True

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return

        lookupteam = self._translateTeam('roto', 'team', optteam)

        url = self._b64decode('aHR0cDovL3d3dy5yb3Rvd29ybGQuY29tL3RlYW1zL2luanVyaWVzL25mbA==') + '/%s/' % lookupteam
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        if soup.find('div', attrs={'class': 'player'}):
            team = soup.find('div', attrs={'class': 'player'}).find('a').getText()
        else:
            irc.reply("No injuries found for: %s" % optteam)
            return

        table = soup.find('table', attrs={'align': 'center', 'width': '600px;'})
        t1 = table.findAll('tr')
        object_list = []
        for row in t1[1:]:
            td = row.findAll('td')
            d = collections.OrderedDict()
            d['name'] = td[0].find('a').text
            d['position'] = td[2].renderContents().strip()
            d['status'] = td[3].renderContents().strip()
            d['date'] = td[4].renderContents().strip().replace("&nbsp;", " ")
            d['injury'] = td[5].renderContents().strip()
            d['returns'] = td[6].renderContents().strip()
            object_list.append(d)

        if len(object_list) < 1:
            irc.reply("No injuries for: %s" % optteam)
            return

        if details:
            irc.reply("{0} - {1} total injuries".format(self._ul(team), len(object_list)))
            irc.reply("{0:25} {1:3} {2:15} {3:<7} {4:<15} {5:<10}".format("Name","POS","Status","Date","Injury","Returns"))

            for inj in object_list:
                output = "{0:27} {1:<3} {2:<15} {3:<7} {4:<15} {5:<10}".format(self._bold( \
                    inj['name']),inj['position'],inj['status'],inj['date'],inj['injury'],inj['returns'])
                irc.reply(output)
        else:
            irc.reply("{0} - {1} total injuries".format(self._ul(team), len(object_list)))
            irc.reply(" | ".join([item['name'] + " (" + item['returns'] + ")" for item in object_list]))

    nflinjury = wrap(nflinjury, [getopts({'details':''}), ('somethingWithoutSpaces')])

    def nflvaluations(self, irc, msg, args):
        """
        Display current NFL team valuations from Forbes.
        """

        url = self._b64decode('aHR0cDovL3d3dy5mb3JiZXMuY29tL25mbC12YWx1YXRpb25zL2xpc3Qv')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        tbody = soup.find('tbody', attrs={'id':'listbody'})
        rows = tbody.findAll('tr')

        append_list = []

        for row in rows:
            tds = row.findAll('td')
            rank = tds[0].getText()
            team = tds[1].getText()
            value = tds[2].getText().replace(',','')  # value needs some mixing and to a float.
            append_list.append("{0}. {1} ({2})".format(rank, self._bold(team), self._millify(float(value)*(1000000))))

        header = self._red("Current NFL Team Values")
        irc.reply("{0} :: {1}".format(header, " | ".join(append_list)))

    nflvaluations = wrap(nflvaluations)

    def nflpowerrankings(self, irc, msg, args, optteam):
        """[team]
        Display this week's NFL Power Rankings.
        Optional: use [team] to display specific commentary. Ex: ATL
        """

        if optteam:  # if we have a team, check if its valid.
            # test for valid teams.
            optteam = self._validteams(optteam)
            if not optteam: # team is not found in aliases or validteams.
                irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
                return

        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wb3dlcnJhbmtpbmdz')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process HTML
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        datehead = soup.find('div', attrs={'class':'date floatleft'})
        table = soup.find('table', attrs={'class':'tablehead'})
        headline = table.find('tr', attrs={'class':'stathead'})
        rows = table.findAll('tr', attrs={'class':re.compile('^oddrow|^evenrow')})

        powerrankings = []  # list to hold each one.
        prtable = {}

        for row in rows:  # one row per team.
            teamdict = {}  # teamdict to put into powerrankings list.
            tds = row.findAll('td')  # findall tds.
            rank = tds[0].getText()  # rank number.
            team = tds[1].find('div', attrs={'style':'padding:10px 0;'}).find('a').getText()  # finds short.
            shortteam = self._translateTeam('team', 'short', str(team))  # small abbreviation via the db.
            lastweek = tds[2].find('span', attrs={'class':'pr-last'}).getText().replace('Last Week:','').strip()  # rank #
            comment = tds[3].getText()  # comment.
            # check if we're up or down and insert a symbol.
            if int(rank) < int(lastweek):
                symbol = self._green('▲')
            elif int(rank) > int(lastweek):
                symbol = self._red('▼')
            else:  # - if the same.
                symbol = "-"

            # now add the rows to our data structures.
            powerrankings.append("{0}. {1} (prev: {2} {3})".format(rank,shortteam,symbol,lastweek))
            prtable[str(shortteam)] = "{0}. {1} (prev: {2} {3}) {4}".format(rank,team,symbol,lastweek,comment)

        # now output. conditional if we have the team or not.
        if not optteam:  # no team so output the list.
            irc.reply("{0} :: {1}".format(self._blue(headline.getText()), datehead.getText()))
            for N in self._batch(powerrankings, 12):  # iterate through each team. 12 per line
                #
                irc.reply("{0}".format(" | ".join([item for item in N])))
        else:  # find the team and only output that team.
            output = prtable.get(str(optteam), None)
            if not output:
                irc.reply("I could not find: %s - Something must have gone wrong." % optteam)
                return
            else:
                irc.reply("{0} :: {1}".format(self._blue(headline.getText()), datehead.getText()))
                irc.reply("{0}".format(output))

    nflpowerrankings = wrap(nflpowerrankings, [optional('somethingWithoutSpaces')])

    def nflschedule(self, irc, msg, args, optlist, optteam):
        """[--full] <team>
        Display the last and next five upcoming games for team.
        Use --full to display the entire 16 game schedule.
        Ex: NE
        """

        fullSchedule = False
        for (option, arg) in optlist:
            if option == 'full':
                fullSchedule = True

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return

        lookupteam = self._translateTeam('eid', 'team', optteam) # don't need a check for 0 here because we validate prior.

        if fullSchedule: # diff url/method.
            lookupteam = self._translateTeam('eid', 'team', optteam) # don't need a check for 0 here because we validate prior.
            url = self._b64decode('aHR0cDovL20uZXNwbi5nby5jb20vbmZsLw==') + 'teamschedule?teamId=%s&wjb=' % str(lookupteam)
            html = self._httpget(url)
            if not html:
                irc.reply("ERROR: Failed to fetch {0}.".format(url))
                self.log.error("ERROR opening {0}".format(url))
                return

            soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
            table = soup.find('table', attrs={'class':'table'})
            # make sure we have table
            rows = table.findAll('tr')
            # container for output
            schedule = []
            # process these rows.
            for row in rows[1:]:
                tds = row.findAll('td')
                if tds[0]['class'].startswith('ind') and len(tds) == 3:
                    gamedate = tds[0].getText()
                    opp = tds[1].getText()
                    result = tds[2].getText()
                    schedule.append("{0} - {1} - {2}".format(gamedate, opp, result))
            # prep for output.
            descstring = " | ".join([item for item in schedule])
            output = "{0} SCHED :: {1}".format(ircutils.mircColor(optteam, 'red'), descstring)
            irc.reply(output)
        else:  # short schedule.
            lookupteam = self._translateTeam('yahoo', 'team', optteam) # don't need a check for 0 here because we validate prior.
            url = self._b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbmZsL3RlYW1z') + '/%s/calendar/rss.xml' % lookupteam
            html = self._httpget(url)
            if not html:
                irc.reply("ERROR: Failed to fetch {0}.".format(url))
                self.log.error("ERROR opening {0}".format(url))
                return

            # clean this stuff up
            html = html.replace('<![CDATA[','').replace(']]>','').replace('EDT','').replace('\xc2\xa0',' ')

            soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
            items = soup.find('channel').findAll('item')

            append_list = []

            for item in items:
                title = item.find('title').renderContents().strip() # title is good.
                day, date = title.split(',')
                desc = item.find('description') # everything in desc but its messy.
                desctext = desc.findAll(text=True) # get all text, first, but its in a list.
                descappend = (''.join(desctext).strip()) # list transform into a string.
                if not descappend.startswith('@'): # if something is @, it's before, but vs. otherwise.
                    descappend = 'vs. ' + descappend
                descappend += " [" + date.strip() + "]"
                append_list.append(descappend) # put all into a list.

            descstring = " | ".join([item for item in append_list])
            output = "{0} {1}".format(self._bold(optteam), descstring)
            irc.reply(output)

    nflschedule = wrap(nflschedule, [(getopts({'full':''})), ('somethingWithoutSpaces')])

    def nflcountdown(self, irc, msg, args):
        """    Display the time until the next NFL season starts.    """

        dDelta = datetime.datetime(2014, 9, 5, 21, 30) - datetime.datetime.now()
        irc.reply("There are {0} days {1} hours {2} minutes {3} seconds until the start of the 2014 NFL Season.".format(\
                                            dDelta.days, dDelta.seconds/60/60, dDelta.seconds/60%60, dDelta.seconds%60))

    nflcountdown = wrap(nflcountdown)

    class PffTeam:
        def __init__(self,pffRow=None):
            if pffRow:
                self.FullName = pffRow[0].getText()
                self.OffOverall = pffRow[1].getText()
                self.OffPass = pffRow[2].getText()
                self.OffRush = pffRow[3].getText()
                self.OffPassBlock = pffRow[4].getText()
                self.OffRunBlock = pffRow[5].getText()
                self.OffPen = pffRow[6].getText()
                self.DefOverall= pffRow[7].getText()
                self.DefRun = pffRow[8].getText()
                self.DefPassRush = pffRow[9].getText()
                self.DefPassCoverage = pffRow[10].getText()
                self.DefPen = pffRow[11].getText()
                self.SpecialTeams = pffRow[12].getText()
            else:
                self.FullName = "Not Found"

    def pffTeamLookup(self, team, optyear):
        try:  # try to see if each key is set.
            pffCookie = self.registryValue('pffCookie')
        except:  # a key is not set, break and error.
            self.log.debug("Failed checking keys. We're missing the config value for: {0}. Please set this and try again.".format('pffCookie'))
            irc.reply("No PFF cookie set")

        teamAbbrev = self._validteams(team)
        teamFull = self._translateTeam('full', 'team', teamAbbrev)

        # defaults to latest year/1st round. input can change this otherwise.
        if optyear:  # test year.
            testdate = self._validate(optyear, '%Y')
            if not testdate:  # invalid year.
                irc.reply("ERROR: Invalid year. Must be YYYY.")
                return
            if not 2007 <= optyear <= datetime.datetime.now().year:
                irc.reply("ERROR: Year must be after 2007 and before the current year.")
                return

        url = self._b64decode('aHR0cHM6Ly93d3cucHJvZm9vdGJhbGxmb2N1cy5jb20vZGF0YS9ieV90ZWFtLnBocD90YWI9YnlfdGVhbQ==')	

        if optyear:  # add year if we have it.
            url += '?SEASON=%s' % (optyear)

        html = self._httpget(url, h={'Cookie':pffCookie},d={})

        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'class':'sortable'})
        h2 = 'PFF Team Stats' #soup.find('h2').getText().strip()
        rows = table.findAll('tr')
        # container list for output.
        object_list = []
        # iterate over each row which is a pick.
        for row in rows:  # string is constructed conditionally.
            tds = row.findAll('td')
            if len(tds) == 13:
                pickTeam = tds[0].getText()
                if pickTeam == teamFull:
                    return self.PffTeam(tds)


    def pffteam(self, irc, msg, args, optteam, optyear):
        """[Team Name] [YYYY] 
        Display overall PFF team stats for a given season    """

        teamStats = self.pffTeamLookup(optteam,optyear)
        appendString = ""

        if teamStats:
            appendString = self._bold("Offense")
            appendString += "(OVR:" + teamStats.OffOverall + ", PAS/RSH: " + teamStats.OffPass + "/" + teamStats.OffRush + ", PassBlk/Runblk:" + teamStats.OffPassBlock + "/" + teamStats.OffRunBlock + ") "
            appendString += self._bold("Defense")
            appendString += "(OVR:" + teamStats.DefOverall + ", Run/Rush/Cvr: " + teamStats.DefRun + "/" + teamStats.DefPassRush + "/" + teamStats.DefPassCoverage + ") "
            # self.OffPen = pffRow[6].getText()
            # self.DefPen = pffRow[11].getText()
            appendString += "ST: " + teamStats.SpecialTeams
        else:  
            appendString = ""

        # output time.
        irc.reply("{0} :: {1}".format(self._red(teamStats.FullName), appendString))

    pffteam = wrap(pffteam, [optional('somethingWithoutSpaces'), optional('int')])

    def nfldraft(self, irc, msg, args, optyear, optround):
        """[YYYY] [round #]
        Show the NFL draft round from year. Year must be 1996 or after and optional round must be between 1 and 7.
        Defaults to round 1 if round is not given.
        Ex: nfldraft 2000 6 (Would show the 6th round of the 2000 draft)
        """

        # defaults to latest year/1st round. input can change this otherwise.
        if optyear:  # test year.
            testdate = self._validate(optyear, '%Y')
            if not testdate:  # invalid year.
                irc.reply("ERROR: Invalid year. Must be YYYY.")
                return
            if not 1967 <= optyear <= datetime.datetime.now().year:
                irc.reply("ERROR: Year must be after 1967 and before the current year.")
                return
        if optround:  # if round.
            if not 1 <= optround <= 7:
                irc.reply("ERROR: Draft round must be between 1 and 7.")
                return
        # construct url. add parameters depending on opts above.
        url = self._b64decode('aHR0cDovL2luc2lkZXIuZXNwbi5nby5jb20vbmZsL2RyYWZ0L3JvdW5kcw==')
        if optyear:  # add year if we have it.
            url += '?year=%s' % (optyear)
        if optround:  # optional round.
            url += '&round=%s' % (optround)
        # build and fetch url.
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # sanity check before we process html.
        if "There is currently no pick data available." in html:
            irc.reply("ERROR: I did not find any draft pick data available for that year.")
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'class':'tablehead draft-tracker'})
        h2 = soup.find('h2').getText().strip()
        rows = table.findAll('tr', attrs={'class': re.compile('^oddrow.*?|^evenrow.*?')})
        # container list for output.
        object_list = []
        # iterate over each row which is a pick.
        for row in rows:  # string is constructed conditionally.
            pickNumber = row.find('p', attrs={'class':'round-number'}).getText()
            pickName = row.find('p', attrs={'class':'player-name'})
            pickTeam = row.find('p', attrs={'class':'team-name'}).getText()
            if pickName:
                appendString = "{0}. {1} - {2}".format(self._bold(pickNumber), pickName.getText(), pickTeam)
            else:  # we won't have a pick leading up to the draft.
                appendString = "{0}. {1}".format(self._bold(pickNumber), pickTeam)
            if row.find('p', attrs={'class':'notes'}):  # if we have notes, add them.
                appendString += " ({0})".format(row.find('p', attrs={'class':'notes'}).getText())
            object_list.append(appendString)  # append.
        # output time.
        irc.reply("{0} :: {1}".format(self._red(h2), " | ".join([i for i in object_list])))

    nfldraft = wrap(nfldraft, [optional('int'), optional('int')])

    def nfltrades(self, irc, msg, args):
        """
        Display the last NFL 5 trades.
        """

        url = self._b64decode('aHR0cDovL3d3dy5zcG90cmFjLmNvbS9uZmwtdHJhZGUtdHJhY2tlci8=')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        table = soup.find('table', attrs={'border':'0'})
        tbodys = table.findAll('tbody')
        # list for output
        nfltrade_list = []
        # each tbody for days. lump it all together.
        for tbody in tbodys:
            rows = tbody.findAll('tr')
            for row in rows:
                player = row.find('td', attrs={'class':'player'}).find('a').getText()
                data = row.find('span', attrs={'class':'data'}).getText()
                date = row.findPrevious('th', attrs={'class':'tracker-date'}).getText()
                fromteam = row.findAll('td', attrs={'class':'playerend'})[0].find('img')['src'].split('/', 7)
                toteam = row.findAll('td', attrs={'class':'playerend'})[1].find('img')['src'].split('/', 7)
                # translate into TEAMS.
                fromteam = self._translateTeam('team','st', fromteam[6].replace('.png', ''))  # have to use silly
                toteam = self._translateTeam('team','st', toteam[6].replace('.png', ''))  # .png method with both.
                # create string. apppend.
                appendString = "{0} :: {1}{2}{3} :: {4} {5}".format(date, self._bold(fromteam), self._red('->'), self._bold(toteam), player, data)
                nfltrade_list.append(appendString)
        # output time.
        irc.reply("Last 5 NFL Trades")
        # now output the first 5.
        for each in nfltrade_list[0:5]:
            irc.reply(each)

    nfltrades = wrap(nfltrades)

    def nflarrests(self, irc, msg, args):
        """
        Display the last 6 NFL Arrests from NFL Nation.
        """

        # build and fetch url.
        url = self._b64decode('aHR0cDovL2FycmVzdG5hdGlvbi5jb20vY2F0ZWdvcnkvcHJvLWZvb3RiYWxsLw==')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        lastDate = soup.findAll('span', attrs={'class': 'time'})[0]
        divs = soup.findAll('div', attrs={'class': 'entry'})
        # list container for output.
        arrestlist = []
        # each div is an arrest.
        for div in divs:
            title = div.find('h2').getText().encode('utf-8')
            datet = div.find('span', attrs={'class': 'time'}).getText().encode('utf-8')
            datet = self._dtFormat("%m/%d", datet, "%B %d, %Y")  # translate date.
            arrestedfor = div.find('strong', text=re.compile('Team:'))
            if arrestedfor:  # if we found strong, regex to parse it out.
                matches = re.search(r'<strong>Team:.*?</strong>(.*?)<br />', arrestedfor.findParent('p').renderContents(), re.I| re.S| re.M)
                if matches:  # we found what we needed.
                    college = matches.group(1).replace('(NFL)','').encode('utf-8').strip()
                else:  # make it easy with "No Team"
                    college = "No team"
            else:  # same. Make it easy with "No Team".
                college = "No team"
            charge = div.find('strong', text=re.compile('Charge:|Charges:'))  # find if we have charges.
            if charge:  # if we found strong, regex to parse it out.
                charges = re.search(r'<strong>Charge.*?</strong>(.*?)<br />', charge.findParent('p').renderContents(), re.I| re.S| re.M)
                if charges:  # we found what we needed.
                    charge = charges.group(1) # .encode('utf-8').strip()
                else:  # something went wrong so don't add.
                    charge = None
            else:  # didn't find so something probably broke.
                charge = None
            if charge:  # if we find a charge, add it.
                arrestlist.append("{0} :: {1} - {2} - {3}".format(datet, title, college, charge))
            else:  # if not, don't add a charge.
                arrestlist.append("{0} :: {1} - {2}".format(datet, title, college))
        # now prepare to output.
        # date math. cacls days between last arrest and today.
        delta = datetime.datetime.strptime(str(lastDate.getText()), "%B %d, %Y").date() - datetime.date.today()
        daysSince = abs(delta.days)
        irc.reply("{0} days since last NFL arrest".format(self._red(daysSince)))
        for each in arrestlist[0:6]:  # print the last 6.
            irc.reply(each)

    nflarrests = wrap(nflarrests)

    def nfltotalqbr(self, irc, msg, args, optlist):
        """[--postseason]
        Display the top10 NFL QBs, ranked by Total QBR.
        Use --postseason to display for postseason.
        """

        postseason = False
        for (option, arg) in optlist:
            if option == 'postseason':
                postseason = True

        if postseason:
            url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9xYnIvXy9zZWFzb250eXBlLzM=')
        else:
            url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9xYnI=')

        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        title = soup.find('div', attrs={'class': 'mod-header stathead'}).find('h4')
        table = soup.find('table', attrs={'class': 'tablehead'})
        rows = table.findAll('tr', attrs={'class': re.compile('^(odd|even)row.*')})[0:10]

        qbrlist = []

        for row in rows:
            rank = row.find('td', attrs={'align':'left'})
            name = rank.findNext('td').find('a')
            qbr = name.findNext('td', attrs={'class':'sortcell'})
            qbrlist.append("{0}. {1} {2}".format(rank.getText(), self._bold(name.getText()), qbr.getText()))

        output = " | ".join([item for item in qbrlist])
        irc.reply("{0}: {1}".format(self._red(title.text), output))

    nfltotalqbr = wrap(nfltotalqbr, [(getopts({'postseason':''}))])

    def nflcoach(self, irc, msg, args, optteam):
        """<team>
        Display the coach for team.
        Ex: NYJ
        """

        # test for valid teams.
        optteam = self._validteams(optteam)
        if not optteam: # team is not found in aliases or validteams.
            irc.reply("ERROR: Team not found. Valid teams are: {0}".format(self._allteams()))
            return
        # fetch url.
        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9jb2FjaGVz')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        div = soup.find('div', attrs={'id': 'my-players-table'})
        table = div.find('table', attrs={'class': 'tablehead'})
        rows = table.findAll('tr', attrs={'class': re.compile('(odd|even)row')})
        # container for output.
        coachlist = collections.defaultdict(list)
        # iterate over each row.
        for row in rows:
            tds = row.findAll('td')
            coach = tds[0].getText().replace("  "," ")
            exp = tds[1].getText()
            team = tds[3].getText()
            team = self._translateTeam('team', 'full', team.strip())
            coachlist[str(team)] = "{0}({1})".format(coach, exp)

        output = coachlist.get(str(optteam))
        if not output:  # didn't found.
            irc.reply("ERROR: Something went horribly wrong looking up the coach for {0}.".format(optteam))
            return
        else:  # found.
            irc.reply("The NFL coach for {0} is {1}".format(self._red(optteam), output))

    nflcoach = wrap(nflcoach, [('somethingWithoutSpaces')])

    def nflnews(self, irc, msg, args):
        """
        Display the latest headlines from nfl.com.
        """

        # build and fetch url.
        url = self._b64decode('aHR0cDovL3MzLmFtYXpvbmF3cy5jb20vbmZsZ2MvYWxsX25ld3NMaXN0Lmpz')
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # try and process json.
        try:
            jsondata = json.loads(html)['content']
        except:
            irc.reply("ERROR: Failed to parse article json from: {0}".format(url))
            return
        # iterate through and output.
        for article in jsondata[0:6]:
            title = article.get('title')
            # desc = article.get('description')
            link = article.get('linkURL')
            # date = article.get('date_ago')
            if title and link:
                irc.reply("{0} - {1}".format(self._bold(title), self._shortenUrl(link)))

    nflnews = wrap(nflnews)

    def nflgamestats(self, irc, msg, args, optteam):
        """<team>

        Fetch live or previous game stats for team.
        Use exact or near exact team name.
        Ex: Patriots OR Rams OR Falcons
        """

        url = self._b64decode('aHR0cDovL3Njb3Jlcy5lc3BuLmdvLmNvbS9uZmwvc2NvcmVib2FyZA==')
        # build and fetch url.
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
            # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        divs = soup.findAll('div', attrs={'id':re.compile('^\d+-gameContainer')})
        # check to make sure we found games.
        if len(divs) == 0:
            irc.reply("ERROR: Something went wrong trying to find games on page. Formatting change?")
            return
        # container to match our games.
        games = {}
        # process games
        for div in divs:
            gameid = div['id'].replace('-gameContainer', '')
            # p id="330915001-aNameOffset"
            ateam = div.find('p', attrs={'id':'%s-aNameOffset' % gameid})
            hteam = div.find('p', attrs={'id':'%s-hNameOffset' % gameid})
            # clean-up the names for better matching.
            ateam = ateam.getText().lower().strip().replace('&amp;', '&').replace('.', '')
            hteam = hteam.getText().lower().strip().replace('&amp;', '&').replace('.', '')
            games[ateam] = gameid  # inject away
            games[hteam] = gameid  # inject home.

        # we must match input (teamname) with a gameid.
        if optteam.lower() in games:
            gid = games[optteam.lower()]
        else:
            output = " | ".join(sorted(games.keys()))
            irc.reply("ERROR: I did not find any games with team: {0} in it. I do have: {1}".format(optteam, output))
            return

        # build and fetch url.
        url = self._b64decode('aHR0cDovL3Njb3Jlcy5lc3BuLmdvLmNvbS9uZmwvYm94c2NvcmU/Z2FtZUlkPQ==') + '%s' % (gid)
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return

        # sanity check before we process.
        if 'Box score not currently available.' in html:
            irc.reply("ERROR: Box score is currently unavailable at: {0} . Checking too early?".format(url))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        tsh4 = soup.find('h4', text="Team Stat Comparison")
        if not tsh4:  # sanity check.
            irc.reply("ERROR: Something went wrong finding Team Stats in gameid: {0}. Checking too early?".format(gid))
            return
        # we find teamstats here.
        tscontent = tsh4.findNext('table', attrs={'class':'mod-data'})  # table for teamstats.
        tshead = tscontent.find('thead')  # find the thead.
        tsteams = tshead.findAll('th', attrs={'nowrap':'nowrap'})  # find the 2x TH with teams.
        teams = {}  # teams container
        for i, tsteam in enumerate(tsteams):  # iterate over these two.
            teams[i] = tsteam.getText().replace('&amp;', '&')  # inject into the dict.
        # now find the tbody with stats.
        tsrows = tscontent.findAll('tr', attrs={'class':re.compile('odd|even')})
        tsstats = collections.defaultdict(list)  # container for the stats.
        for tsrow in tsrows:  # iterate over rows. each row has two tds.
            tds = tsrow.findAll('td')  # find all tds. There should be three per row.
            stat = tds[0].getText()  # statname. below, we shorten things.
            # 1st Downs: 22 | Passing 1st downs: 12 | Rushing 1st downs: 8 | 1st downs from Penalties: 2 | 3rd down efficiency: 7-17 |
            # 4th down efficiency: 0-1 | Total Plays: 79 | Total Yards: 407 | Yards per play: 5.2 | Total Drives: 12 | Passing: 280 |
            # Comp - Att: 25-45 | Yards per pass: 6.2 | Interceptions thrown: 0 | Sacks - Yards Lost: 0-0 | Rushing: 127 | Rushing Attempts: 34 | Yards
            # per rush: 3.7 | Red Zone (Made-Att): 1-2 | Penalties: 9-84 | Turnovers: 0 | Fumbles lost: 0 | Interceptions thrown: 0 |
            # Defensive / Special Teams TDs: 0 | Possession: 35:34
            stat = stat.replace('1st Downs', '1stD').replace('Passing 1st downs', 'Pass').replace('Rushing 1st downs', 'Rush').replace('1st downs from Penalties', 'Pen')
            stat = stat.replace('3rd down efficiency', '3rdD EFF').replace('4th down efficiency', '4thD EFF').replace('Total Plays', 'Plys').replace('Total Yards', 'YDS')
            stat = stat.replace('Yards per play', 'YPP').replace('Total Drives', 'Drvs').replace('Comp - Att', 'C-A').replace('Defensive / Special Teams TDs', 'D/ST TDs')
            stat = stat.replace('Yards per pass', 'YPP').replace('Interceptions thrown', 'INT').replace('Sacks - Yards Lost', 'S-YL')
            stat = stat.replace('Rushing Attempts', 'R-ATT').replace('Yards per rush', 'YPR').replace('Red Zone (Made-Att)', 'RZ (Made-Att)').replace('Penalties', 'PEN')
            stat = stat.replace('Turnovers', 'TO').replace('Fumbles lost', 'Fum lst').replace('Intercceptions thrown', 'INT')
            stat = stat.replace('Possession', 'TOP').replace('Passing', 'PASS').replace('Rushing', 'RUSH')
            tsstats[teams[0]].append("{0}: {1}".format(self._bold(stat), tds[1].getText()))  # inject away stats. bold category.
            tsstats[teams[1]].append("{0}: {1}".format(self._bold(stat), tds[2].getText()))  # inject home stats. bolc category.
        # now we prepare to output.
        for (z, y) in tsstats.items():  # k = teamname, v = list of stats.
            irc.reply("{0} :: {1}".format(self._red(z), " | ".join(y)))

    nflgamestats = wrap(nflgamestats, [('text')])

    ########################################
    # NFL PLAYER DATABASE PUBLIC FUNCTIONS #
    ########################################

    def nflplayers(self, irc, msg, args, optlist, optname):
        """<player>

        Search and find NFL players. Must enter exact/approx name since no fuzzy matching is done here.
        Ex: Tom Brady
        """

        # handle getopts/optlist input.
        showFull = False  # setup variables.
        if optlist:  # if we have input.
            if 'full' in dict(optlist):  # if --full is specified.
                showFull = True  # showFull is on.

        optplayer = self._sanitizeName(optname)  # sanitize optname.
        with sqlite3.connect(self._playersdb) as db:  # connect to db.
            cursor = db.cursor()  # below, we select eid/rid/fullname %name% and replace spaces in name with %.
            cursor.execute("SELECT eid, rid, fullname FROM players WHERE fullname LIKE ? ORDER BY eid", ('%'+optplayer.replace(' ','%')+'%',))
            rows = cursor.fetchall()
        # check if we found anything.
        if len(rows) == 0:
            irc.reply("ERROR: Sorry, I did not find any players matching {0}".format(optname))
            return
        # we did find stuff, so. otherwise, output.
        if not showFull:  # regular search, no full results.
            irc.reply("Matching players found({0}): {1}".format(len(rows), " | ".join(sorted([i[2] for i in rows]))))
        else:  # show full results.
            for i, row in enumerate(rows):  # iterate through. eid=row[0], rid=row[1], fullname=row[2]
                rowout = "| {0:6} | {1:6} | {2:30} |".format(row[0], row[1], row[2])
                if i == 0:  # first row. so we want our header below.
                    irc.reply("| {0:>6} | {1:>6} | {2:<30} |".format("EID","RID","NAME"))
                    irc.reply("|{0:{1}<8}|{0:{1}<8}|{0:{1}<32}|".format("", "-"))
                    irc.reply(rowout)
                elif i == 6:  # if we're here, we already printed 5 results.
                    irc.reply("Sorry, I found too many results for '{0}' (Total: {1}). Try something more specific.".format(optname, len(rows)))
                    break
                else:  # all other rows.
                    irc.reply(rowout)

    nflplayers = wrap(nflplayers, [getopts({'full':''}), ('text')])

    def nflplayernews(self, irc, msg, args, optplayer):
        """<player>

        Display latest news for NFL player.
        Ex: Tom Brady
        """

        useSPN = False  # simple bypass as I found wrold but am not sure how long it will work.
        if useSPN:  # conditional to use SPN here. We'll use rworld.
            if optplayer.isdigit():  # test if we get a num, so we bypass the playerlookup.
                lookupid = optplayer  # if it is, set.
            else:  # else, lookup the playername.
                lookupid = self._playerLookup('eid', optplayer)  # we get a str back or list. str="found", list="not found".
                if isinstance(lookupid, list):  # if we have a list back, it means something is wrong.
                    related = ' | '.join([i['fullname'].title() for i in lookupid])  # join just the fullnames in Title.
                    irc.reply("ERROR: No player found for: '{0}'. Maybe you were looking for: {1}".format(optplayer, related))
                    return
            # build and fetch url.
            url = self._b64decode('aHR0cDovL20uZXNwbi5nby5jb20vbmZsL3BsYXllcnVwZGF0ZQ==') + '?playerId=%s&wjb=' % lookupid
            html = self._httpget(url)
            if not html:
                irc.reply("ERROR: Failed to fetch {0}.".format(url))
                self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
                return
            # process html.
            soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
            playerName = soup.find('div', attrs={'class': 'sub bold'})
            if not playerName:  # sanity check here.
                irc.reply("I could not find any news. Did formatting change?")
                return
            else:  # get their formal name.
                playerName = playerName.getText()
            # find the div where "news" is in.
            if soup.find('div', attrs={'class': 'ind line'}):
                playerNews = soup.find('div', attrs={'class': 'ind line'})
                extraPlayerNews = playerNews.find('div', attrs={'style': 'font-style:italic;'})
                if extraPlayerNews:  # clean it up.
                    extraPlayerNews.extract()
                    playerNews = self._remove_accents(playerNews.getText())
                else:
                    playerNews = "No news found for player."
        else:  # use rworld.
            if optplayer.isdigit():  # test if we get a num, so we bypass the playerlookup.
                lookupid = optplayer  # set the id as the digit itself.
            else:  # else, lookup the playername.
                lookupid = self._playerLookup('rid', optplayer)  # we get a str back or list. str="found", list="not found".
                if isinstance(lookupid, list):  # if we have a list back, it means something is wrong.
                    related = ' | '.join([i['fullname'].title() for i in lookupid])  # join just the fullnames in Title.
                    irc.reply("ERROR: No player found for: '{0}'. Maybe you were looking for: {1}".format(optplayer, related))
                    return
                # make sure it's valid.
                if lookupid == '':
                    irc.reply("ERROR: I do not have a valid RID for {0}. My apologies.".format(optplayer))
                    return
            # all is good. lets go.
            # build and fetch url.
            url = self._b64decode('aHR0cDovL2Rldi5yb3Rvd29ybGQuY29tL3NlcnZpY2VzL21vYmlsZS5hc214L0dldEpTT05TaW5nbGVQbGF5ZXJOZXdzP3Nwb3J0PU5GTA==') + '&playerid=%s' % lookupid
            html = self._httpget(url)
            if not html:
                irc.reply("ERROR: Failed to fetch {0}.".format(url))
                self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
                return
            # parse json
            jsondata = json.loads(html)
            # check to make sure we have news and build output string.
            if len(jsondata) < 1:  # generic error here.
                playerNews = "I did not find any news for player."
            else:  # we did find some playernews.
                jsondata = jsondata[0]
                playerName = jsondata['FirstName'] + " " + jsondata['LastName']
                timestamp = jsondata.get('TimeStamp')  # RawTimeStamp
                headline = jsondata.get('Headline')
                impact = jsondata.get('Impact')
                news = jsondata.get('News')
                # now construct playernews string for output.
                playerNews = ""
                if timestamp: playerNews += "{0}".format(timestamp)
                if headline: playerNews += " {0}".format(headline.encode('utf-8').replace('&quot;', '"'))
                if news: playerNews += " {0}".format(news.encode('utf-8').replace('&quot;', '"'))
                if impact: playerNews += " {0}".format(impact.encode('utf-8').replace('&quot;', '"'))  #self._remove_accents(impact))

        # finally, lets output. this works with both methods above.
        output = "{0} :: {1}".format(self._red(playerName), utils.str.normalizeWhitespace(playerNews))
        irc.reply(output)

    nflplayernews = wrap(nflplayernews, [('text')])

    def nflinfo(self, irc, msg, args, optplayer):
        """<player>

        Display basic information on NFL player.
        Ex: Tom Brady
        """

        if optplayer.isdigit():  # test if we get a num, so we bypass the playerlookup.
            lookupid = optplayer  # if it is, set.
        else:  # else, lookup the playername.
            lookupid = self._playerLookup('eid', optplayer)  # we get a str back or list. str="found", list="not found".
            if isinstance(lookupid, list):  # if we have a list back, it means something is wrong.
                related = ' | '.join([i['fullname'].title() for i in lookupid])  # join just the fullnames in Title.
                irc.reply("ERROR: No player found for: '{0}'. Maybe you were looking for: {1}".format(optplayer, related))
                return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvXy9pZC8=') + '%s' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        # find the main div
        div = soup.find('div', attrs={'class':'mod-container mod-no-header-footer mod-page-header'})
        if not div:
            irc.reply("ERROR: I could not find active information for player.")
            return
        playerName = div.find('h1')
        # setup our output container with the name.
        out = []
        # basic stats.
        stats = div.find('ul', attrs={'class':'general-info'})
        for stat in stats:  # iterate over and add the text.
            out.append(stat.getText())
        # find the rest of the bio
        bios = div.find('ul', attrs={'class':'player-metadata floatleft'}).findAll('li')
        for bios in bios:
            cat = bios.find('span')  # span is the category.
            cat.extract()  # now extract the span because bios = rest of text we want.
            out.append("{0}: {1}".format(cat.getText(), bios.getText()))
        # prepare output.
        descstring = " | ".join([item for item in out])
        output = "{0} :: {1}".format(self._red(playerName.getText()), descstring)
        irc.reply(output)

    nflinfo = wrap(nflinfo, [('text')])

    def nflcontract(self, irc, msg, args, optplayer):
        """<player>

        Display NFL contract for player.
        Ex: Tom Brady
        """

        if optplayer.isdigit():  # test if we get a num, so we bypass the playerlookup.
            lookupid = optplayer  # if it is, set.
        else:  # else, lookup the playername.
            lookupid = self._playerLookup('rid', optplayer)  # we get a str back or list. str="found", list="not found".
            if isinstance(lookupid, list):  # if we have a list back, it means something is wrong.
                related = ' | '.join([i['fullname'].title() for i in lookupid])  # join just the fullnames in Title.
                irc.reply("ERROR: No player found for: '{0}'. Maybe you were looking for: {1}".format(optplayer, related))
                return
        # sanity check for rid because I sometimes lack them.
        if lookupid == '':
            irc.reply("ERROR: I have {0} in the DB but no RID for them. Please visit roto for the player's contract.".format(optplayer))
            return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL3d3dy5yb3Rvd29ybGQuY29tL3BsYXllci9uZmwv') + '%s/' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return
        # process HTML.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        pn = soup.find('div', attrs={'class':'playercard', 'style':'display:none;', 'id': re.compile('^cont_.*')})
        if not pn:  # check and make sure we have a contract.
            irc.reply("ERROR: No contract found for: {0}".format(optplayer))
            return
        # format/parse html.
        h1 = soup.find('h1').getText().split('|',1)[0].strip()
        p1 = pn.find('div', attrs={'class': 'report'}).getText()
        contract = re.sub('<[^<]+?>', '', p1).strip()
        contract = utils.str.normalizeWhitespace(contract)  # kill double spacing.
        # output
        irc.reply("{0} :: {1}".format(self._red(h1), contract))

    nflcontract = wrap(nflcontract, [('text')])

    def nflgame(self, irc, msg, args, optplayer):
        """<player>

        Display NFL player's game log for current/active/previous game.
        Ex: Eli Manning
        """

        if optplayer.isdigit():  # test if we get a num, so we bypass the playerlookup.
            lookupid = optplayer  # if it is, set.
        else:  # else, lookup the playername.
            lookupid = self._playerLookup('eid', optplayer)  # we get a str back or list. str="found", list="not found".
            if isinstance(lookupid, list):  # if we have a list back, it means something is wrong.
                related = ' | '.join([i['fullname'].title() for i in lookupid])  # join just the fullnames in Title.
                irc.reply("ERROR: No player found for: '{0}'. Maybe you were looking for: {1}".format(optplayer, related))
                return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvXy9pZA==') + '/%s/' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return
        # sanity check before processing.
        if "No statistics available." in html:
            irc.reply("ERROR: No statistics found on the player page for: {0}".format(optplayer.title()))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        h4 = soup.find('h4', text="CURRENT GAME")
        if not h4:
            h4 = soup.find('h4', text="PREVIOUS GAME")
            if not h4:
                irc.reply("ERROR: I could not find game statistics for: {0}. Player not playing? Also try nflgamelog command.".format(optplayer.title()))
                return
            else:  # for previous game, find the date/time/status
                gametype = soup.find('div', attrs={'class':'time'}).getText(separator=' ')
        else:  # for current game, we should find the game "status"
            gametype = soup.find('div', attrs={'class':'time'}).getText(separator=' ')
        # now process html for actual gamestats.
        div = h4.findParent('div').findParent('div')
        # find the playername
        playerName = soup.find('div', attrs={'class':'mod-container mod-no-header-footer mod-page-header'}).find('h1')
        # team, number and position.
        playerTeam = soup.find('div', attrs={'class':'player-bio'}).find('ul', attrs={'class':'general-info'}).find('li', attrs={'class':'last'})
        table = div.find('table', attrs={'class':'tablehead'})
        # need another sanity check here..
        if not table:
            irc.reply("ERROR: I could not find any statistics for: {0}. Sure you typed in the right player?".format(playerName.getText()))
            return
        # this is an odd "bug" where a player is active but no stats are displayed.
        thisgame = table.findAll('tr')[1].find('td')  # 2nd row (first non header) and first cell.
        if thisgame.getText() != "This Game":
            irc.reply("ERROR: I could not find any active statistics for: {0} in active game. This happens when player is active for the game but has not played a down.".format(playerName.getText()))
            return
        # we did find... continue.
        header = table.find('tr', attrs={'class':'colhead'}).findAll('th')[1:]
        row = table.findAll('tr')[1].findAll('td')[1:]
        # output.
        output = " | ".join([self._bold(each.getText()) + ": " + row[i].getText() for i, each in enumerate(header)])
        irc.reply("{0} ({1}) :: {2} :: {3}".format(self._red(playerName.getText()), playerTeam.getText(), gametype, output))

    nflgame = wrap(nflgame, [('text')])

    def _postostats(self, pos):
        """What to look for with each position. For nflcareerstats and nflseason."""

        table = {
            'QB': ['passing', 'rushing'],
            'RB': ['rushing', 'receiving'],
            'FB': ['rushing', 'receiving'],
            'WR': ['receiving', 'rushing'],
            'TE': ['receiving', 'rushing'],
            'DE': ['defensive'],
            'DT': ['defensive'],
            'LB': ['defensive'],
            'CB': ['defensive'],
            'S': ['defensive'],
            'PK': ['kicking'],
            'P': ['punting'] }
        # now work with the table.
        if pos in table:
            return table[pos]
        else:  # not in table.
            return None

    def nflcareerstats(self, irc, msg, args, optplayer):
        """<player>

        Look up NFL career stats for a player.
        Ex: Tom Brady
        """

        if optplayer.isdigit():  # test if we get a num, so we bypass the playerlookup.
            lookupid = optplayer  # if it is, set.
        else:  # else, lookup the playername.
            lookupid = self._playerLookup('eid', optplayer)  # we get a str back or list. str="found", list="not found".
            if isinstance(lookupid, list):  # if we have a list back, it means something is wrong.
                related = ' | '.join([i['fullname'].title() for i in lookupid])  # join just the fullnames in Title.
                irc.reply("ERROR: No player found for: '{0}'. Maybe you were looking for: {1}".format(optplayer, related))
                return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvc3RhdHMvXy9pZA==') + '/%s/' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return
        # one last sanity check before we process html.
        if "No stats available." in html:
            irc.reply("No stats available for: {0}. Perhaps they play a position without formal stats?".format(optplayer))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        if not soup.find('a', attrs={'class': 'btn-split-btn'}): # check if player is active.
            irc.reply("ERROR: Cannot find any career stats for an inactive/unsigned player: %s" % optplayer)
            return
        # experience.
        exp = soup.find('span', text="Experience")
        if exp:
            exp = exp.findParent('li')
            exp.span.extract()
            exp = exp.getText()
        else:  # didn't find it.
            exp = "None."
        # position. make sure we have it.
        pos = soup.find('ul', attrs={'class': 'general-info'})
        if pos:  # we found it.
            pos = pos.find('li', attrs={'class': 'first'})
            if pos:  # we found the second part.
                pos = pos.getText().upper()  # get text and go upper.
                pos = ''.join([eachLetter for eachLetter in pos if eachLetter.isalpha()])  # iffy but works.
            else:  # something went wrong. Check formatting?
                irc.reply("ERROR: I could not find player's position. Check formatting.")
                return
        else:  # something went wrong. Check formatting?
            irc.reply("ERROR: I could not find player's position. Check formatting.")
            return
        # basics.
        playername = soup.find('a', attrs={'class': 'btn-split-btn'}).getText().strip()
        article = soup.find('div', attrs={'class': 'article'})
        divs = article.findAll('table', attrs={'class': 'tablehead'})  # each one.
        # prepare dicts for output
        stats = {}  # holds the actual stats
        statcategories = {}  # holds the categories.
        # expanded careerstats. something can go wrong here but we'll fix when something goes wrong.
        for f, div in enumerate(divs):
            if div.find('tr', attrs={'class': 'colhead'}):
                if not div.find('tr', attrs={'class': 'total'}, text="There are no stats available."):
                    stathead = div.find('tr', attrs={'class': 'stathead'})
                    colhead = div.find('tr', attrs={'class': 'colhead'}).findAll('td')[1:]
                    totals = div.find('tr', attrs={'class': 'total'}).findAll('td')[1:]
                    tmplist = []
                    for i, total in enumerate(totals):
                        tmplist.append(self._bold(colhead[i+1].getText()) + ": " + total.getText())
                    stats[int(f)] = tmplist
                    statcategories[str(stathead.getText().replace('Stats', '').strip().lower())] = f
        # prepare output string.
        careerstats = {}
        # grab the stat categories for this position.
        statcats = self._postostats(pos)
        if not statcats:  # something went wrong.
            irc.reply("ERROR: I don't know what categories to fetch for the {0} category. Check formatting.".format(pos))
            return
        # we're good so lets process.
        for each in statcats:  # iterate over what we get back.
            if each in statcategories:
                careerstats[each.title()] = " | ".join(stats.get(statcategories[each]))
        # something went wrong finding those stats.
        if len(careerstats) == 0:
            irc.reply("ERROR: I could not find {0} stats for {1}. Check formatting?".format(" ,".join(statcats), playername))
            return
        # everything went ok. now output.
        irc.reply("{0}({1} exp) career stats :: {2}".format(self._red(playername), exp, " || ".join([self._ul(k) + " " + v for (k, v) in careerstats.items()])))

    nflcareerstats = wrap(nflcareerstats, [('text')])

    def nflseason(self, irc, msg, args, optlist, optplayer):
        """[--year DDDD] <player>

        Look up NFL Season stats for a player.
        To look up a different year, use --year YYYY.
        Ex: Tom Brady or --year 2010 Tom Brady
        """

        # handle optinput for season.
        season = False
        if optlist:
            for (key,value) in optlist:
                if key == 'year': # check our year. validate below.
                    season = self._validate(str(value), '%Y')
                    if not season:
                        irc.reply("ERROR: {0} is an invalid year. Must be YYYY.".format(value))
                        return
                    else:
                        season = str(value)
        # if we do not have the season, determine the last season and calculate.
        if not season:
            if datetime.datetime.now().month < 9:  # if we're before September, last year.
                season = str(datetime.datetime.now().year - 1)
            elif datetime.datetime.now().month == "9" and datetime.datetime.now().day < 9:  # before Sept 9th.
                season = str(datetime.datetime.now().year - 1)
            else:  # else, use current year.
                season = str(datetime.datetime.now().year)
        # now that we have the season, lookup the player.
        if optplayer.isdigit():  # test if we get a num, so we bypass the playerlookup.
            lookupid = optplayer  # if it is, set.
        else:  # else, lookup the playername.
            lookupid = self._playerLookup('eid', optplayer)  # we get a str back or list. str="found", list="not found".
            if isinstance(lookupid, list):  # if we have a list back, it means something is wrong.
                related = ' | '.join([i['fullname'].title() for i in lookupid])  # join just the fullnames in Title.
                irc.reply("ERROR: No player found for: '{0}'. Maybe you were looking for: {1}".format(optplayer, related))
                return
        # build and fetch url.
        url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvc3RhdHMvXy9pZA==') + '/%s/' % lookupid
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0} looking up {1}".format(url, optplayer))
            return
        # sanity check before processing html.
        if "No stats available." in html:
            irc.reply("ERROR: No stats available for: {0}".format(optplayer))
            return
        # process html.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        if not soup.find('a', attrs={'class':'btn-split-btn'}):  # check if player is active.
            irc.reply("ERROR: Cannot find any season stats for an inactive/unsigned player: {0}".format(optplayer))
            return
        # basics.
        playername = soup.find('a', attrs={'class':'btn-split-btn'}).getText().strip()
        # position. make sure we have it.
        pos = soup.find('ul', attrs={'class': 'general-info'})
        if pos:  # we found it.
            pos = pos.find('li', attrs={'class': 'first'})
            if pos:  # we found the second part.
                pos = pos.getText().upper()  # get text and go upper.
                pos = ''.join([eachLetter for eachLetter in pos if eachLetter.isalpha()])  # iffy but works.
            else:  # something went wrong. Check formatting?
                irc.reply("ERROR: I could not find {0} position. Check formatting.".format(playername))
                return
        else:  # something went wrong. Check formatting?
            irc.reply("ERROR: I could not find {0} position. Check formatting.".format(playername))
            return
        # grab the stat categories for this position.
        statcats = self._postostats(pos)
        if not statcats:  # something went wrong.
            irc.reply("ERROR: I don't know what categories to fetch for the {0} category. Check formatting.".format(pos))
            return
        # container for output.
        stats = collections.defaultdict(dict)
        # find the divs in the html with stats.
        divs = soup.findAll('div', attrs={'class':'mod-container mod-table mod-player-stats'})
        for div in divs:  # iterate over
            #t = collections.defaultdict(dict)
            stattype = div.find('tr', attrs={'class':'stathead'}).getText().lower().replace(' stats', '')  # stattype.
            if stattype in statcats:
                table = div.find('table', attrs={'class':'tablehead'})  # table in each.
                headings = table.find('tr', attrs={'class':'colhead'}).findAll('td') # headings in each table
                rows = table.findAll('tr', attrs={'class': re.compile('^oddrow|^evenrow')})  # each row is a year.
                for row in rows:  # iterate over each row (season)
                    tds = row.findAll('td')  # grab tds to process.
                    year = tds[0].getText()  # first row = year
                    if year == season:  # only grab stats for the year we need. +1 so we don't reprint the season.
                        yearstats = [self._bold(headings[i+1].getText()) + ": " + n.getText() for (i, n) in enumerate(tds[1:])]
                        stats[stattype] = yearstats  # inject.
        # output.
        if len(stats) == 0:  # this means we didn't find something above. Could be buggy but it's quick.
            irc.reply("ERROR: I could not find stats in year {0} for {1}".format(season, playername))
            return
        # we're good. we found stats. lets print them.
        output = " || ".join([self._bu(k.title()) + ": " + " ".join([i for i in v]) for (k, v) in stats.items()])
        irc.reply("{0} :: {1} SEASON :: {2}".format(self._red(playername), season, output))

    nflseason = wrap(nflseason, [(getopts({'year': ('int')})), ('text')])

    def nflgamelog(self, irc, msg, args, optlist, optplayer):
        """[--year DDDD | --game #] <player>

        Display gamelog from previous or specific game.
        If --game # is not specified, it tries to print the last gamelog.
        If --year #### is not specified, it defaults to the current NFL year.
        Ex: Tom Brady OR --game 2 Eli Manning OR --year 2012 --game 2 Eli Manning
        """

        # define variables.
        optgame, optyear = False, None
        # handle getopts (optlist)
        if optlist:
            for (key, value) in optlist:
                if key == 'year':  # year, test, optdate if true
                    testdate = self._validate(value, '%Y')
                    if not testdate:
                        irc.reply("ERROR: Invalid year. Must be YYYY.")
                        return
                    else:
                        optyear = value #url += 'year/%s' % value
                if key == 'game':  # what game?
                    if not 1 <= int(value) <= 21:  # 1->21
                        irc.reply("ERROR: '{0}' is an invalid game. Must be between 1-21.".format(value))
                        return
                    else:  # game was good.
                        optgame = value

        # now lookup the player.
        if optplayer.isdigit():  # test if we get a num, so we bypass the playerlookup.
            lookupid = optplayer  # if it is, set.
        else:  # else, lookup the playername.
            lookupid = self._playerLookup('eid', optplayer)  # we get a str back or list. str="found", list="not found".
            if isinstance(lookupid, list):  # if we have a list back, it means something is wrong.
                related = ' | '.join([i['fullname'].title() for i in lookupid])  # join just the fullnames in Title.
                irc.reply("ERROR: No player found for: '{0}'. Maybe you were looking for: {1}".format(optplayer, related))
                return

        # build and fetch url.
        if optyear:  # if we have optyear from above:
            url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvZ2FtZWxvZy9fL2lkLw==') + '%s/year/%s/' % (lookupid, str(optyear))
        else:
            url = self._b64decode('aHR0cDovL2VzcG4uZ28uY29tL25mbC9wbGF5ZXIvZ2FtZWxvZy9fL2lkLw==') + '%s/' % (lookupid)
        html = self._httpget(url)
        if not html:
            irc.reply("ERROR: Failed to fetch {0}.".format(url))
            self.log.error("ERROR opening {0}".format(url))
            return
        # first, a sanity check.
        if 'No stats available.' in html:
            irc.reply("ERROR: Something broke loading stats for: {0}. Check to make sure year is correct or formatting did not change.".format(optplayer))
            return
        # process html. put some additional error checks in because it can be iffy.
        soup = BeautifulSoup(html, convertEntities=BeautifulSoup.HTML_ENTITIES, fromEncoding='utf-8')
        div = soup.find('div', attrs={'class':'mod-container mod-table mod-player-stats'})
        # more sanity checks.
        if not div:  # one check.
            irc.reply("ERROR: Something broke loading the gamelog. Player might have no stats or gamelog due to position.")
            return
        table = div.find('table', attrs={'class':'tablehead'})
        if not table:  # second check.
            irc.reply("ERROR: Something broke loading the gamelog. Player might have no stats or gamelog due to position.")
            return
        # we're good so lets grab our html.
        playername = soup.find('a', attrs={'class':'btn-split-btn'}).getText().strip()
        stathead = table.find('tr', attrs={'class':'stathead'}).findAll('td')
        header = table.find('tr', attrs={'class':'colhead'}).findAll('td')
        rows = table.findAll('tr', attrs={'class': re.compile('^oddrow.*?|^evenrow.*?')})
        selectedyear = soup.find('select', attrs={'class':'tablesm'}).find('option', attrs={'selected':'selected'})
        # last check before we process the data.
        if len(rows) < 1 or len(header) < 1 or len(stathead) < 1:
            irc.reply("ERROR: I did not find any gamelog data for: {0} (Check formatting on gamelog page).".format(optplayer))
            return
        # now, lets get to processing the data.
        # this is messy but the only way I thought to handle the colspan situation.
        # below, we make a list and iterate in order over stathead tds.
        # statheadlist uses enum to insert, in order found (since the dict gets reordered if you don't)
        # each entry in statheadlist is a dict of colspan:heading, like:
        # {0: {'3': '2012 REGULAR SEASON GAME LOG'}, 1: {'10': 'PASSING'}, 2: {'5': 'RUSHING'}}
        statheaddict = {}
        for e, blah in enumerate(stathead):
            tmpdict = {}
            tmpdict[str(blah['colspan'])] = str(blah.text)
            statheaddict[e] = tmpdict
        # now, we have the statheadlist, create statheadlist to be the list of
        # each header[i] colspan element, where you can use its index value to ref.
        # so, if header[i] = QBR, the "parent" td colspan is PASSING.
        # ex: ['2012 REGULAR SEASON GAME LOG', '2012 REGULAR SEASON GAME LOG',
        # '2012 REGULAR SEASON GAME LOG', 'PASSING', 'PASSING', ... 'RUSHING'
        statheadlist = []
        for q, x in sorted(statheaddict.items()):  # sorted dict, x is the "dict" inside.
            for k, v in x.items():  # key = colspan, v = the td parent header
                for each in range(int(k)):  # range the number to insert.
                    # do some replacement (truncating) because we use this in output.
                    v = v.replace('PASSING','PASS').replace('RUSHING','RUSH').replace('PUNTING','PUNT-')
                    v = v.replace('RECEIVING','REC').replace('FUMBLES','FUM').replace('TACKLES','TACK')
                    v = v.replace('INTERCEPTIONS','INT').replace('FIELD GOALS','FG').replace('PATS','XP')
                    statheadlist.append(v)  # add to list.
        # now, we put all of the data into a data structure
        gamelist, games = {}, {}  # gamelist dict. one game per entry. games contains a list incase there is an error.
        # go through each row and extract, mate with header.
        for i, row in enumerate(rows):
            d = {}  # everything in an OD for calc/sort later.
            tds = row.findAll('td')  # all td in each row.
            week = i+1  # add in the week but +1 for human reference later.
            for f,td in enumerate(tds):  # within each round, there are tds w/data.
                if f > 2:  # the first three will be game log parts, so append statheadlist from above.
                    if str(statheadlist[f]) == str(header[f].getText()):  # check if key is there like INT so we don't double include
                        d[header[f].getText()] = td.getText()  # this will just look normal like XPM or INT
                    else:  # regular "addtiion" where it is something like FUM-FF
                        d[statheadlist[f] + "-" + header[f].getText()] = td.getText()
                else:  # td entries 2 and under like DATE, OPP, RESULT
                    d[header[f].getText()] = td.getText()  # inject all into the OD.
            # we also add into games so we can print to the user a list of games we have.
            games[week] = tds[1].getText()
            # finally, each game and its data in OD now injected into object_list.
            gamelist[week] = d

        # find optgame if we don't have it.
        if not optgame:
            optgame = max(games.keys())  # highest number (or last game)
        # output time.
        outputgame = gamelist.get(optgame)
        if not outputgame:  # handle finding the game or not for output.
            g = " | ".join([str(k) + ": " + v for (k, v) in sorted(games.items())])
            irc.reply("ERROR: I did not find game number {0} in {1} for {2}. I do have: {3}".format(optgame, selectedyear.getText(), playername, g))
            return
        else:  # we did find an outputgame, so go out.
            output = " | ".join([self._bold(z) + ": " + x for (z, x) in sorted(outputgame.items())])
            # finally output on irc.
            irc.reply("{0} :: W{1} :: {2}".format(self._red(playername), optgame, output))

    nflgamelog = wrap(nflgamelog, [getopts({'year':('int'), 'game':('int')}), ('text')])

Class = NFL

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=250:
