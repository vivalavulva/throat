# -*- coding: utf-8
""" Here is where all the good stuff happens """

import json
import time
import re
import random
import cgi
from wsgiref.handlers import format_date_time
import datetime
from urllib.parse import urlparse, urljoin

import bcrypt
import markdown
from sqlalchemy import func, or_
from flask import Flask, render_template, session, redirect, url_for, abort, g
from flask import make_response, request
from flask_assets import Environment, Bundle
from flask_login import LoginManager, login_required, current_user
# from flask_sqlalchemy import get_debug_queries
from tld import get_tld
from werkzeug.contrib.atom import AtomFeed
from feedgen.feed import FeedGenerator

from .forms import RegistrationForm, LoginForm, LogOutForm, EditSubFlair
from .forms import CreateSubForm, EditSubForm, EditUserForm, EditSubCSSForm
from .forms import CreateSubTextPost, EditSubTextPostForm, CreateSubLinkPost
from .forms import CreateUserMessageForm, PostComment, EditModForm
from .forms import DeletePost, CreateUserBadgeForm, EditMod2Form, DummyForm
from .forms import EditSubLinkPostForm, BanUserSubForm, EditPostFlair
from .forms import CreateSubFlair, UseBTCdonationForm
from .views import do, api
from .views.api import oauth
from . import misc, forms, caching
from . import database as db
from .misc import SiteUser, isMod
from .misc import SiteAnon, getAnnouncement
from .misc import getSubUsers, getSubCreation, getSuscriberCount, getModCount
from .misc import getSubPostCount, isRestricted, isNSFW
from .misc import userCanFlair, getPostFlair
from .misc import enableBTCmod
from .sorting import VoteSorting, BasicSorting, HotSorting, NewSorting
# from werkzeug.contrib.profiler import ProfilerMiddleware
import logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.jinja_env.cache = {}

# app.config['PROFILE'] = True
# app.wsgi_app = ProfilerMiddleware(app.wsgi_app)

app.register_blueprint(do)
app.register_blueprint(api)
app.config.from_object('config')
if app.config['TESTING']:
    import logging
    logging.basicConfig()
    logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# db.init_app(app)
oauth.init_app(app)
caching.cache.init_app(app)

assets = Environment(app)
login_manager = LoginManager(app)
login_manager.anonymous_user = SiteAnon
origstatic = app.view_functions['static']


def cache_static(*args, **kwargs):
    """ We use this to make a far-expiry cache when we serve the assets
    directly (like we do in Heroku). This makes the browser just use the cached
    resources instead of checking if they changed and getting a 302
    response. """
    response = make_response(origstatic(*args, **kwargs))
    expires_time = time.mktime((datetime.datetime.now() +
                                datetime.timedelta(days=365)).timetuple())

    response.headers['Cache-Control'] = 'public, max-age=31536000'
    response.headers['Expires'] = format_date_time(expires_time)
    return response
app.view_functions['static'] = cache_static

# We use nested bundles here. One of them is for stuff that is already minified
# And the other is for stuff that we have to minify. This makes the
# bundle-making process a bit faster.
js = Bundle(
    Bundle('js/jquery.min.js',
           'js/magnific-popup.min.js',
           'js/bootstrap.buttons.min.js',
           'js/CustomElements.min.js'),
    Bundle('js/time-elements.js',
           'js/konami.js',
           'js/markdown.js',
           'js/bootstrap-markdown.js',
           'js/site.js', filters='jsmin'),
    output='gen/site.js')
css = Bundle(
    Bundle('css/font-awesome.min.css',
           'css/bootstrap-markdown.min.css'),
    Bundle('css/magnific-popup.css', 'css/style.css',
           filters='cssmin,datauri'), output='gen/site.css')

assets.register('js_all', js)
assets.register('css_all', css)


@app.teardown_appcontext
def close_db(error):
    """Closes the database again at the end of the request."""
    if hasattr(g, 'dbmod'):
        g.db.commit()

    if hasattr(g, 'db'):
        g.db.close()


@app.before_request
def do_magic_stuff():
    g.appconfig = app.config

# @app.before_first_request
# def initialize_database():
#     """ This is executed before any request is processed. We use this to
#     create all the tables and database shit we need. """
#     db.create_all()


@login_manager.user_loader
def load_user(user_id):
    """ This is used by flask_login to reload an user from a previously stored
    unique identifier. Required for the 'remember me' functionality. """
    user = db.get_user_from_uid(user_id)
    if not user:
        return None
    else:
        return SiteUser(user)


@app.before_request
def before_request():
    """ Called before the request is processed. Used to time the request """
    g.start = time.time()


@app.after_request
def after_request(response):
    """ Called after the request is processed. Used to time the request """
    if not app.debug and not current_user.is_admin():
        return response  # We won't do this if we're in production mode
    diff = time.time() - g.start
    diff = int(diff * 1000)
    if app.debug:
        print("Exec time: %s ms" % str(diff))

    if not hasattr(g, 'qc'):
        g.qc = 0
    if response.response and isinstance(response.response, list):
        etime = str(diff).encode()

        response.response[0] = response.response[0] \
                                       .replace(b'__EXECUTION_TIME__', etime)
        response.response[0] = response.response[0] \
                                       .replace(b'__DB_QUERIES__',
                                                str(g.qc).encode())
        response.headers["content-length"] = len(response.response[0])
    return response


@app.context_processor
def utility_processor():
    """ Here we set some useful stuff for templates """
    # TODO: Kill this huge mass of shit
    return {'loginform': LoginForm(), 'regform': RegistrationForm(),
            'logoutform': LogOutForm(), 'sendmsg': CreateUserMessageForm(),
            'csubform': CreateSubForm(), 'markdown': misc.our_markdown,
            'commentform': PostComment(), 'dummyform': DummyForm(),
            'delpostform': DeletePost(),
            'config': app.config, 'form': forms, 'db': db,
            'getSuscriberCount': getSuscriberCount, 'func': misc}


@app.route("/")
def index():
    """ The index page, shows /hot of current subscriptions """
    return home_hot(1)


@app.route("/hot", defaults={'page': 1})
@app.route("/hot/<int:page>")
def home_hot(page):
    """ /hot for subscriptions """
    subs = misc.getSubscriptions(current_user.get_id())
    posts = misc.getPostsFromSubs(subs)

    sorter = HotSorting(posts)
    return render_template('index.html', page=page, sort_type='home_hot',
                           posts=sorter.getPosts(page))


@app.route("/new", defaults={'page': 1})
@app.route("/new/<int:page>")
def home_new(page):
    """ /new for subscriptions """
    subs = misc.getSubscriptions(current_user.get_id())
    posts = misc.getPostsFromSubs(subs)
    sorter = NewSorting(posts)
    return render_template('index.html', page=page, sort_type='home_new',
                           posts=sorter.getPosts(page))


@app.route("/top", defaults={'page': 1})
@app.route("/top/<int:page>")
def home_top(page):
    """ /top for subscriptions """
    subs = misc.getSubscriptions(current_user.get_id())
    posts = misc.getPostsFromSubs(subs)

    sorter = VoteSorting(posts)
    return render_template('index.html', page=page, sort_type='home_top',
                           posts=sorter.getPosts(page))


@app.route("/all/new.rss")
def all_new_rss():
    """ RSS feed for /all/new """
    fg = FeedGenerator()
    fg.title("/all/new")
    fg.subtitle("All new posts feed")
    fg.link(href=url_for('all_new', _external=True))
    fg.generator("Throat")
    c = db.query('SELECT * FROM `sub_post` ORDER BY `posted` DESC LIMIT 30')
    posts = c.fetchall()
    sorter = BasicSorting(posts)
    for post in sorter.getPosts():
        fe = fg.add_entry()
        url = url_for('view_post', sub=misc.getSub(post['sid'])['name'],
                      pid=post['pid'],
                      _external=True)
        fe.id(url)
        fe.link({'href': url, 'rel': 'self'})
        fe.title(post['title'])

    return fg.rss_str(pretty=True)


@app.route("/all/new", defaults={'page': 1})
@app.route("/all/new/<int:page>")
def all_new(page):
    """ The index page, all posts sorted as most recent posted first """
    c = db.query('SELECT * FROM `sub_post` ORDER BY `posted` DESC '
                 'LIMIT %s,20', ((page - 1) * 20, ))
    posts = c.fetchall()
    # sorter = BasicSorting(posts)

    return render_template('index.html', page=page, sort_type='all_new',
                           posts=posts)


@app.route("/domain/<domain>", defaults={'page': 1})
@app.route("/domain/<domain>/<int:page>")
def all_domain_new(page, domain):
    """ The index page, all posts sorted as most recent posted first """
    c = db.query('SELECT * FROM `sub_post` WHERE `link` LIKE %s '
                 'LIMIT %s,20', ('%://' + domain + '/%', (page - 1) * 20))
    posts = c.fetchall()
    return render_template('domains.html', page=page, domain=domain,
                           sort_type='all_domain_new',
                           posts=posts)


@app.route("/search/<term>", defaults={'page': 1})
@app.route("/search/<term>/<int:page>")
def search(page, term):
    """ The index page, with basic title search """
    c = db.query('SELECT * FROM `sub_post` WHERE `title` LIKE %s '
                 'ORDER BY `posted` DESC LIMIT %s,20',
                 ('%' + term + '%', (page - 1) * 20))
    posts = c.fetchall()

    return render_template('index.html', page=page, sort_type='all_new',
                           posts=posts)


@app.route("/all/top", defaults={'page': 1})
@app.route("/all/top/<int:page>")
def all_top(page):
    """ The index page, all posts sorted as most recent posted first """
    c = db.query('SELECT * FROM `sub_post` ORDER BY `score` ASC LIMIT %s,20',
                 ((page - 1) * 20, ))
    posts = c.fetchall()

    return render_template('index.html', page=page, sort_type='all_top',
                           posts=posts)


@app.route("/all/hot", defaults={'page': 1})
@app.route("/all/hot/<int:page>")
def all_hot(page):
    """ The index page, all posts sorted as most recent posted first """
    c = db.query('SELECT * FROM `sub_post` ORDER BY `posted` DESC LIMIT 500 ')
    sorter = HotSorting(c.fetchall())

    return render_template('index.html', page=page, sort_type='all_hot',
                           posts=sorter.getPosts(page))


@app.route("/welcome")
def welcome():
    """ Welcome page for new users """
    return render_template('welcome.html')


@app.route("/donate")
def donate():
    """ Donation page """
    return render_template('donate.html')


@app.route("/subs", defaults={'page': 1})
@app.route("/subs/<int:page>")
def view_subs(page):
    """ Here we can view available subs """
    # TODO: pagination
    c = db.query('SELECT * FROM `sub` ORDER BY `name` DESC')
    return render_template('subs.html', page=page, subs=c.fetchall())


@app.route("/mysubs")
def view_my_subs():
    """ Here we can view subscribed subs """
    # TODO: filter by subscribed subs
    c = db.query('SELECT * FROM `sub` ORDER BY `name`')
    return render_template('subs.html', subs=c.fetchall())


@app.route("/random")
def random_sub():
    """ Here we get a random sub """
    c = db.query('SELECT `name` FROM `sub` ORDER BY RAND() LIMIT 1')
    return redirect(url_for('view_sub', sub=c.fetchone()['name']))


@app.route("/s/<sub>")
def view_sub(sub):
    """ Here we can view subs """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    x = db.get_sub_metadata(sub['sid'], 'sort')
    if not x or x['value'] == 'v':
        return redirect(url_for('view_sub_hot', sub=sub['name']))
    elif x['value'] == 'v_two':
        return redirect(url_for('view_sub_new', sub=sub['name']))
    elif x['value'] == 'v_three':
        return redirect(url_for('view_sub_top', sub=sub['name']))


@app.route("/s/<sub>/edit/css")
@login_required
def edit_sub_css(sub):
    """ Here we can edit sub info and settings """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    if not current_user.is_mod(sub) and not current_user.is_admin():
        abort(403)

    form = EditSubCSSForm()

    form.css.data = db.get_sub_stylesheet(sub['sid'])
    return render_template('editsubcss.html', sub=sub, form=form)


@app.route("/s/<sub>/edit/flairs")
@login_required
def edit_sub_flairs(sub):
    """ Here we manage the sub's flairs. """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    if not current_user.is_mod(sub) and not current_user.is_admin():
        abort(403)

    c = db.query('SELECT * FROM `sub_flair` WHERE `sid`=%s', (sub['sid'], ))
    flairs = c.fetchall()
    formflairs = []
    for flair in flairs:
        formflairs.append(EditSubFlair(flair=flair['xid'], text=flair['text']))
    return render_template('editflairs.html', sub=sub, flairs=formflairs,
                           createflair=CreateSubFlair())


@app.route("/s/<sub>/edit")
@login_required
def edit_sub(sub):
    """ Here we can edit sub info and settings """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    if current_user.is_mod(sub) or current_user.is_admin():
        form = EditSubForm(subsort=db.get_sub_metadata(sub['sid'], 'sort'))
        form.sidebar.data = sub['sidebar']
        return render_template('editsub.html', sub=sub, editsubform=form)
    else:
        abort(403)


@app.route("/s/<sub>/sublog")
@login_required
def view_sublog(sub):
    """ Here we can see a log of mod/admin activity in the sub """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    logs = db.query('SELECT * FROM `sub_log` WHERE `sid`=%s', (sub['sid'], ))
    logs = logs.fetchall()
    return render_template('sublog.html', sub=sub, logs=logs)


@app.route("/s/<sub>/mods")
@login_required
def edit_sub_mods(sub):
    """ Here we can edit moderators for a sub """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    if current_user.is_mod(sub) or current_user.is_modinv(sub) \
       or current_user.is_admin():
        xmods = db.get_sub_metadata(sub['sid'], 'xmod2', _all=True)
        mods = db.get_sub_metadata(sub['sid'], 'mod2', _all=True)
        modinvs = db.get_sub_metadata(sub['sid'], 'mod2i', _all=True)
        return render_template('submods.html', sub=sub, mods=mods,
                               modinvs=modinvs, xmods=xmods,
                               editmod2form=EditMod2Form(),
                               banuserform=BanUserSubForm())
    else:
        abort(403)


@app.route("/s/<sub>/new.rss")
def sub_new_rss(sub):
    """ RSS feed for /s/sub/new """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    fg = FeedGenerator()
    fg.title("/s/{}".format(sub['name']))
    fg.subtitle("All new posts for {} feed".format(sub['name']))
    fg.link(href=url_for('view_sub_new', sub=sub['name'], _external=True))
    fg.generator("Throat")
    posts = db.query('SELECT * FROM `sub_post` WHERE sid=%s'
                     ' ORDER BY `posted` DESC LIMIT 30', (sub['sid'], )) \
              .fetchall()

    for post in posts:
        fe = fg.add_entry()
        url = url_for('view_post', sub=sub['name'],
                      pid=post['pid'], _external=True)
        fe.id(url)
        fe.link({'href': url, 'rel': 'self'})
        fe.title(post['title'])

    return fg.rss_str(pretty=True)


@app.route("/s/<sub>/new", defaults={'page': 1})
@app.route("/s/<sub>/new/<int:page>")
def view_sub_new(sub, page):
    """ The index page, all posts sorted as most recent posted first """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    posts = db.query('SELECT * FROM `sub_post` WHERE `sid`=%s '
                     'ORDER BY `posted` DESC LIMIT %s,20',
                     (sub['sid'], (page - 1) * 20, )).fetchall()
    mods = db.get_sub_metadata(sub['sid'], 'mod2', _all=True)
    createtxtpost = CreateSubTextPost(sub=sub['name'])
    createlinkpost = CreateSubLinkPost(sub=sub['name'])

    return render_template('sub.html', sub=sub, page=page,
                           sort_type='view_sub_new',
                           posts=posts, mods=mods,
                           txtpostform=createtxtpost,
                           lnkpostform=createlinkpost)


@app.route("/s/<sub>/bannedusers")
def view_sub_bans(sub):
    """ See banned users for the sub """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    banned = db.get_sub_metadata(sub['sid'], 'ban', _all=True)
    xbans = db.get_sub_metadata(sub['sid'], 'xban', _all=True)
    return render_template('subbans.html', sub=sub, banned=banned,
                           xbans=xbans, banuserform=BanUserSubForm())


@app.route("/s/<sub>/top", defaults={'page': 1})
@app.route("/s/<sub>/top/<int:page>")
def view_sub_top(sub, page):
    """ The index page, /top sorting """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    posts = db.query('SELECT * FROM `sub_post` WHERE `sid`=%s '
                     'ORDER BY `score` LIMIT %s,20',
                     (sub['sid'], (page - 1) * 20, )).fetchall()

    mods = db.get_sub_metadata(sub['sid'], 'mod2', _all=True)
    createtxtpost = CreateSubTextPost(sub=sub['name'])
    createlinkpost = CreateSubLinkPost(sub=sub['name'])

    return render_template('sub.html', sub=sub, page=page,
                           sort_type='view_sub_top',
                           posts=posts, mods=mods,
                           txtpostform=createtxtpost,
                           lnkpostform=createlinkpost)


@app.route("/s/<sub>/hot", defaults={'page': 1})
@app.route("/s/<sub>/hot/<int:page>")
def view_sub_hot(sub, page):
    """ The index page, /hot sorting """
    sub = db.get_sub_from_name(sub)
    if not sub:
        abort(404)

    c = db.query('SELECT * FROM `sub_post` WHERE `sid`=%s LIMIT 500',
                 (sub['sid'], )).fetchall()
    sorter = HotSorting(c)
    mods = db.get_sub_metadata(sub['sid'], 'mod2', _all=True)
    createtxtpost = CreateSubTextPost(sub=sub['name'])
    createlinkpost = CreateSubLinkPost(sub=sub['name'])

    return render_template('sub.html', sub=sub, page=page,
                           sort_type='view_sub_hot',
                           posts=sorter.getPosts(page), mods=mods,
                           txtpostform=createtxtpost,
                           lnkpostform=createlinkpost)


@app.route("/s/<sub>/<pid>")
def view_post(sub, pid):
    """ View post and comments (WIP) """
    post = db.get_post_from_pid(pid)
    ksub = db.get_sub_from_sid(post['sid'])
    if not post or ksub['name'] != sub:
        abort(404)

    editflair = EditPostFlair()
    editflair.flair.choices = []
    if post['uid'] == current_user.get_id() or current_user.is_mod(ksub) \
       or current_user.is_admin():
        flairs = db.query('SELECT `xid`, `text` FROM `sub_flair` '
                          'WHERE `sid`=%s', (ksub['sid'], )).fetchall()
        for flair in flairs:
            editflair.flair.choices.append((flair['xid'], flair['text']))

    mods = db.get_sub_metadata(post['sid'], 'mod2', _all=True)
    txtpedit = EditSubTextPostForm()
    txtpedit.content.data = post['content']
    createtxtpost = CreateSubTextPost(sub=ksub['name'])
    createlinkpost = CreateSubLinkPost(sub=ksub['name'])
    upcount = db.query('SELECT COUNT(*) AS c FROM `sub_post_vote` WHERE '
                       '`pid`=%s AND `positive`=%s', (pid, 1)).fetchone()['c']
    downcount = db.query('SELECT COUNT(*) AS c FROM `sub_post_vote` WHERE '
                         '`pid`=%s AND `positive`=%s', (pid, 0)) \
                  .fetchone()['c']

    return render_template('post.html', post=post, mods=mods,
                           edittxtpostform=txtpedit,
                           upcount=upcount, downcount=downcount,
                           editlinkpostform=EditSubLinkPostForm(),
                           lnkpostform=createlinkpost,
                           txtpostform=createtxtpost, editpostflair=editflair)


@app.route("/p/<pid>")
def view_post_inbox(pid):
    """ Gets route to post from just pid """
    post = db.get_post_from_pid(pid)
    if not post:
        abort(404)
    sub = db.get_sub_from_sid(post['sid'])
    return redirect(url_for('view_post', sub=sub['name'], pid=post['pid']))


@app.route("/c/<cid>")
def view_comment_inbox(cid):
    """ Gets route to post from just cid """
    comm = db.get_comment_from_cid(cid)
    if not comm:
        abort(404)
    post = db.get_post_from_pid(comm['pid'])
    sub = db.get_sub_from_sid(post['sid'])
    return redirect(url_for('view_post', sub=sub['name'], pid=comm['pid']))


@app.route("/s/<sub>/<pid>/<cid>")
def view_perm(sub, pid, cid):
    """ WIP: Permalink to comment, see rTH7ed77c7c69c3,
    currently using :active css to flag comment in comment chain """
    # TODO
    return "WIP!"


@app.route("/u/<user>")
@login_required
def view_user(user):
    """ WIP: View user's profile, posts, comments, badges, etc """
    user = db.get_user_from_name(user)
    if not user:
        abort(404)

    owns = db.get_user_positions(user['uid'], 'mod1')
    mods = db.get_user_positions(user['uid'], 'mod2')
    badges = db.get_user_badges(user['uid'])
    pcount = db.query('SELECT COUNT(*) AS c FROM `sub_post` WHERE `uid`=%s',
                      (user['uid'], )).fetchone()['c']
    ccount = db.query('SELECT COUNT(*) AS c FROM `sub_post_comment` WHERE '
                      '`uid`=%s', (user['uid'], )).fetchone()['c']

    return render_template('user.html', user=user, badges=badges,
                           msgform=CreateUserMessageForm(), pcount=pcount,
                           ccount=ccount, owns=owns, mods=mods)


@app.route("/u/<user>/posts", defaults={'page': 1})
@app.route("/u/<user>/posts/<int:page>")
@login_required
def view_user_posts(user, page):
    """ WIP: View user's recent posts """
    user = db.get_user_from_name(user)
    if not user:
        abort(404)

    owns = db.get_user_positions(user['uid'], 'mod1')
    mods = db.get_user_positions(user['uid'], 'mod2')
    badges = db.get_user_badges(user['uid'])
    posts = db.query('SELECT * FROM `sub_post` WHERE `uid`=%s '
                     'ORDER BY `posted` DESC LIMIT 20 OFFSET %s ',
                     (user['uid'],((page - 1) * 20)))
    return render_template('userposts.html', user=user, badges=badges,
                           msgform=CreateUserMessageForm(), page=page,
                           owns=owns, mods=mods, posts=posts.fetchall())


@app.route("/u/<user>/comments", defaults={'page': 1})
@app.route("/u/<user>/comments/<int:page>")
@login_required
def view_user_comments(user, page):
    """ WIP: View user's recent comments """
    user = db.get_user_from_name(user)
    if not user:
        abort(404)

    owns = db.get_user_positions(user['uid'], 'mod1')
    mods = db.get_user_positions(user['uid'], 'mod2')
    badges = db.get_user_badges(user['uid'])
    comments = db.query('SELECT * FROM `sub_post_comment` WHERE `uid`=%s '
                        'ORDER BY `time` DESC LIMIT 20 OFFSET %s ',
                        (user['uid'],((page - 1) * 20)))
    return render_template('usercomments.html', user=user, badges=badges,
                           msgform=CreateUserMessageForm(), page=page,
                           comments=comments.fetchall(), owns=owns, mods=mods)


@app.route("/u/<user>/edit")
@login_required
def edit_user(user):
    """ WIP: Edit user's profile, slogan, quote, etc """
    user = db.get_user_from_name(user)
    if not user:
        abort(404)

    owns = db.get_user_positions(user['uid'], 'mod1')
    mods = db.get_user_positions(user['uid'], 'mod2')
    badges = db.get_user_badges(user['uid'])
    pcount = db.query('SELECT COUNT(*) AS c FROM `sub_post` WHERE `uid`=%s',
                      (user['uid'], )).fetchone()['c']
    ccount = db.query('SELECT COUNT(*) AS c FROM `sub_post_comment` WHERE '
                      '`uid`=%s', (user['uid'], )).fetchone()['c']
    exlink = int(db.get_user_metadata(user['uid'], 'exlinks'))
    styles = int(db.get_user_metadata(user['uid'], 'nostyles'))
    nsfw = int(db.get_user_metadata(user['uid'], 'nsfw'))
    form = EditUserForm(external_links=bool(exlink), show_nsfw=bool(nsfw),
                        disable_sub_style=bool(styles))
    adminbadges = []
    if current_user.is_admin():
        adminbadges = db.query('SELECT * FROM `user_badge`').fetchall()
    if current_user.get_username() == user['name'] or current_user.is_admin():
        return render_template('edituser.html', user=user, owns=owns,
                               badges=badges, adminbadges=adminbadges,
                               pcount=pcount, ccount=ccount, mods=mods,
                               edituserform=form)
    else:
        abort(403)


@app.route("/messages")
@login_required
def inbox_sort():
    """ Inbox? """
    if current_user.new_pm_count() == 0 \
       and current_user.new_postreply_count() > 0:
        return redirect(url_for('view_messages_postreplies'))
    if current_user.new_pm_count() == 0 \
       and current_user.new_comreply_count() > 0:
        return redirect(url_for('view_messages_comreplies'))
    if current_user.new_pm_count() == 0 \
       and current_user.new_modmail_count() > 0:
        return redirect(url_for('view_messages_modmail'))
    else:
        return redirect(url_for('view_messages'))


@app.route("/messages/inbox")
def view_messages():
    """ WIP: View user's messages """
    user = session['user_id']
    msgs = db.query('SELECT * FROM `message` WHERE `mtype` IN (1, 8)'
                    'AND `receivedby`=%s ORDER BY `posted` DESC',
                    (user,)).fetchall()
    return render_template('messages.html', user=user, messages=msgs,
                           box_name="Inbox", boxID="1")


@app.route("/messages/sent")
@login_required
def view_messages_sent():
    """ WIP: View user's messages """
    user = session['user_id']
    msgs = db.query('SELECT * FROM `message` WHERE `mtype`=1 '
                    'AND `sentby`=%s ORDER BY `posted` DESC',
                    (user,)).fetchall()
    return render_template('messages.html', user=user, messages=msgs,
                           box_name="Sent")


@app.route("/messages/postreplies")
@login_required
def view_messages_postreplies():
    """ WIP: View user's post replies """
    user = session['user_id']
    msgs = db.query('SELECT * FROM `message` WHERE `mtype`=4 '
                    'AND `receivedby`=%s ORDER BY `posted` DESC',
                    (user,)).fetchall()
    return render_template('messages.html', user=user, messages=msgs,
                           box_name="Replies", boxID="2")


@app.route("/messages/commentreplies")
@login_required
def view_messages_comreplies():
    """ WIP: View user's comments replies """
    user = session['user_id']
    msgs = db.query('SELECT * FROM `message` WHERE `mtype`=5 '
                    'AND `receivedby`=%s ORDER BY `posted` DESC',
                    (user,)).fetchall()
    return render_template('messages.html', user=user, messages=msgs,
                           box_name="Replies", boxID="3")


@app.route("/messages/modmail")
@login_required
def view_messages_modmail():
    """ WIP: View user's modmail """
    user = session['user_id']
    msgs = db.query('SELECT * FROM `message` WHERE `mtype` IN (2, 7) '
                    'AND `receivedby`=%s ORDER BY `posted` DESC',
                    (user,)).fetchall()
    return render_template('messages.html', user=user, messages=msgs,
                           box_name="ModMail", boxID="4")


@app.route("/admin")
@login_required
def admin_area():
    """ WIP: View users. assign badges, etc """
    if current_user.is_admin():
        users = db.query('SELECT COUNT(*) AS c FROM `user`').fetchone()['c']
        subs = db.query('SELECT COUNT(*) AS c FROM `sub`').fetchone()['c']
        posts = db.query('SELECT COUNT(*) AS c FROM `sub_post`') \
                  .fetchone()['c']
        ups = db.query('SELECT COUNT(*) AS c FROM `sub_post_vote` WHERE '
                       '`positive`=1').fetchone()['c']
        downs = db.query('SELECT COUNT(*) AS c FROM `sub_post_vote` WHERE '
                         '`positive`=0').fetchone()['c']
        badges = db.query('SELECT * FROM `user_badge`').fetchall()
        btc = db.get_site_metadata('usebtc')
        if btc:
            x = db.get_site_metadata('btcmsg')['value']
            y = db.get_site_metadata('btcaddr')['value']
            btc = UseBTCdonationForm(message=x, btcaddress=y)
        else:
            btc = UseBTCdonationForm()
        return render_template('admin.html', badges=badges, subs=subs,
                               posts=posts, ups=ups, downs=downs, users=users,
                               createuserbadgeform=CreateUserBadgeForm(),
                               usebtcdonationform=btc)
    else:
        return render_template('errors/404.html'), 404


@app.route("/admin/users")
@login_required
def admin_users():
    """ WIP: View users. """
    if current_user.is_admin():
        users = db.query('SELECT * FROM `user` ORDER BY `name`').fetchall()
        return render_template('adminusershome.html', users=users)
    else:
        return render_template('errors/404.html'), 404


@app.route("/admin/users/<term>")
@login_required
def admin_users_search(term):
    """ WIP: View users. """
    if current_user.is_admin():
        users = db.query('SELECT * FROM `user` WHERE `name` LIKE %s'
                         'ORDER BY `name` ASC', ('%' + term + '%',))
        return render_template('adminusers.html', users=users)
    else:
        return render_template('errors/404.html'), 404


@app.route("/admin/subs")
@login_required
def admin_subs():
    """ WIP: View subs. Assign new owners """
    if current_user.is_admin():
        subs = db.query('SELECT * FROM `sub`').fetchall()
        return render_template('adminsubs.html', subs=subs,
                               editmodform=EditModForm())
    else:
        return render_template('errors/404.html'), 404


@app.route("/admin/subs/<term>")
@login_required
def admin_subs_search(term):
    """ WIP: View users. """
    if current_user.is_admin():
        subs = db.query('SELECT * FROM `sub` WHERE `name` LIKE %s'
                        'ORDER BY `name` ASC', ('%' + term + '%',))
        return render_template('adminsubs.html', subs=subs,
                               editmodform=EditModForm())
    else:
        return render_template('errors/404.html'), 404


@app.route("/admin/post")
@login_required
def admin_post():
    """ WIP: View post. """
    if current_user.is_admin():
        # TODO: pagination
        posts = db.query('SELECT * FROM `sub_post` ORDER BY `posted` DESC '
                         'LIMIT 100')
        return render_template('adminpost.html',
                               sort_type='all_new',
                               posts=posts)
    else:
        return render_template('errors/404.html'), 404


@app.route("/admin/post/<term>")
@login_required
def admin_post_search(term):
    """ WIP: View users. """
    if current_user.is_admin():
        post = db.get_post_from_pid(term)
        user = db.get_user_from_uid(post['uid'])
        sub = db.get_sub_from_sid(post['sid'])

        votes = db.query('SELECT * FROM `sub_post_vote` WHERE `pid`=%s',
                         (post['pid'],)).fetchall()
        upcount = db.query('SELECT COUNT(*) AS c FROM `sub_post_vote` WHERE '
                           '`pid`=%s AND `positive`=1', (post['pid'], )) \
                    .fetchone()['c']
        downcount = db.query('SELECT COUNT(*) AS c FROM `sub_post_vote` WHERE '
                             '`pid`=%s AND `positive`=0', (post['pid'], )) \
                      .fetchone()['c']

        pcount = db.query('SELECT COUNT(*) AS c FROM `sub_post` WHERE '
                          '`uid`=%s', (user['uid'],)).fetchone()['c']
        ccount = db.query('SELECT COUNT(*) AS c FROM `sub_post_comment` WHERE '
                          '`uid`=%s', (user['uid'],)).fetchone()['c']

        return render_template('adminpostsearch.html', sub=sub, post=post,
                               votes=votes, ccount=ccount, pcount=pcount,
                               upcount=upcount, downcount=downcount)
    else:
        return render_template('errors/404.html'), 404


@app.route("/admin/sitelog")
@login_required
def view_sitelog():
    """ Here we can see a log of admin activity on the site """
    if current_user.is_admin():
        logs = db.query('SELECT * FROM `site_log` ORDER BY `lid` DESC')
        return render_template('sitelog.html', logs=logs.fetchall())
    else:
        abort(403)


@app.route("/register")
def register():
    """ Endpoint for the registration form """
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route("/recover")
def password_recovery():
    """ Endpoint for the registration form """
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('password_recovery.html')


@app.route('/reset/<uid>/<key>')
def password_reset(uid, key):
    """ The page that actually resets the password """
    user = db.get_user_from_uid(uid)
    if not user:
        abort(403)
    key = db.get_user_metadata(user['uid'], 'recovery-key')
    # keyExp = db.get_user_metadata(user['uid'], 'recovery-key-time')
    if not key:
        abort(404)
    if current_user.is_authenticated:
        db.uquery('DELETE FROM `user_metadata` WHERE `uid`=%s AND `key`=%s',
                  (user['uid'], 'recovery-key'))
        db.uquery('DELETE FROM `user_metadata` WHERE `uid`=%s AND `key`=%s',
                  (user['uid'], 'recovery-key-time'))
        return redirect(url_for('index'))

    form = forms.PasswordResetForm(key=key['value'], user=user['uid'])
    return render_template('password_reset.html', resetpw=form)


@app.route("/api")
def view_api():
    """ View API help page """
    return render_template('api.html')


@app.route("/tos")
def tos():
    """ Shows the site's TOS. """
    return render_template('tos.html')


@app.route("/privacy")
def privacy():
    """ Shows the site's privacy policy. """
    return render_template('privacy.html')


@app.errorhandler(401)
def unauthorized(error):
    """ 401 Unauthorized """
    return render_template('errors/401.html'), 401


@app.errorhandler(403)
def Forbidden(error):
    """ 403 Forbidden """
    return render_template('errors/403.html'), 403


@app.errorhandler(404)
def not_found(error):
    """ 404 Not found error """
    return render_template('errors/404.html'), 404


@app.errorhandler(418)
def teapot(error):
    """ 404 I'm a teapot """
    return render_template('errors/418.html'), 404


@app.errorhandler(500)
def server_error(error):
    """ 500 Internal server error """
    return render_template('errors/500.html'), 500
