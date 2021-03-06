""" admin-related forms """

from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, TextField
from wtforms import IntegerField
from wtforms.validators import DataRequired, Length
from flask_babel import lazy_gettext as _l


class SecurityQuestionForm(FlaskForm):
    """ Create security question """
    question = TextField(_l('Question'), validators=[DataRequired()])
    answer = TextField('Answer', validators=[DataRequired()])


class EditModForm(FlaskForm):
    """ Edit owner of sub (admin) """
    sub = StringField(_l('Sub'),
                      validators=[DataRequired(), Length(min=2, max=128)])
    user = StringField(_l('New owner username'),
                       validators=[DataRequired(), Length(min=1, max=128)])


class AssignUserBadgeForm(FlaskForm):
    """ Assign user badge to user (admin) """
    badge = StringField(_l('Badge nick'),
                      validators=[DataRequired(), Length(min=1, max=128)])
    user = StringField(_l('Username'),
                       validators=[DataRequired(), Length(min=1, max=128)])


class BanDomainForm(FlaskForm):
    """ Add banned domain """
    domain = StringField(_l('Enter Domain'))


class UseInviteCodeForm(FlaskForm):
    """ Enable/Use an invite code to register """
    enableinvitecode = BooleanField(_l('Enable invite code to register'))
    minlevel = IntegerField(_l("Minimum level to create invite codes"))
    maxcodes = IntegerField(_l("Max amount of invites per user"))

class TOTPForm(FlaskForm):
    """ TOTP form for admin 2FA """
    totp = StringField(_l('Enter one-time password'))
