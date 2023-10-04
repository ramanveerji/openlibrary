"""Handlers for adding and editing tags."""

import web
import json

from typing import NoReturn

from infogami.core.db import ValidationException
from infogami.infobase import common
from infogami.utils.view import add_flash_message, public
from infogami.infobase.client import ClientException
from infogami.utils import delegate

from openlibrary.plugins.openlibrary.processors import urlsafe
from openlibrary.i18n import gettext as _
import logging

from openlibrary.plugins.upstream import spamcheck, utils
from openlibrary.plugins.upstream.models import Tag
from openlibrary.plugins.upstream.addbook import get_recaptcha, safe_seeother, trim_doc
from openlibrary.plugins.upstream.utils import render_template

logger = logging.getLogger("openlibrary.tag")


@public
def get_tag_types():
    return ["subject", "work", "collection"]


class addtag(delegate.page):
    path = '/tag/add'

    def GET(self):
        """Main user interface for adding a tag to Open Library."""

        if not self.has_permission():
            raise common.PermissionDenied(message='Permission denied to add tags')

        return render_template('tag/add', recaptcha=get_recaptcha())

    def has_permission(self) -> bool:
        """
        Can a tag be added?
        """
        user = web.ctx.site.get_user()
        return user and (user.is_usergroup_member('/usergroup/super-librarians'))

    def POST(self):
        i = web.input(
            tag_name="",
            tag_type="",
            tag_description="",
            tag_plugins="",
        )

        if spamcheck.is_spam(i, allow_privileged_edits=True):
            return render_template(
                "message.html", "Oops", 'Something went wrong. Please try again later.'
            )

        if not web.ctx.site.get_user():
            recap = get_recaptcha()
            if recap and not recap.validate():
                return render_template(
                    'message.html',
                    'Recaptcha solution was incorrect',
                    'Please <a href="javascript:history.back()">go back</a> and try again.',
                )

        i = utils.unflatten(i)
        if match := self.find_match(i):
            # tag match
            return self.tag_match(match)
        else:
            # no match
            return self.no_match(i)

    def find_match(self, i: web.utils.Storage):
        """
        Tries to find an existing tag that matches the data provided by the user.
        """
        return Tag.find(i.tag_name, i.tag_type)

    def tag_match(self, match: list) -> NoReturn:
        """
        Action for when an existing tag has been found.
        Redirect user to the found tag's edit page to add any missing details.
        """
        tag = web.ctx.site.get(match[0])
        raise safe_seeother(f"{tag.key}/edit")

    def no_match(self, i: web.utils.Storage) -> NoReturn:
        """
        Action to take when no tags are found.
        Creates a new Tag.
        Redirects the user to the tag's home page
        """
        key = Tag.create(i.tag_name, i.tag_description, i.tag_type, i.tag_plugins)
        raise safe_seeother(key)


# remove existing definitions of addtag
delegate.pages.pop('/addtag', None)


class addtag(delegate.page):  # type: ignore[no-redef] # noqa: F811
    def GET(self):
        raise web.redirect("/tag/add")


class tag_edit(delegate.page):
    path = r"(/tags/OL\d+T)/edit"

    def GET(self, key):
        if not web.ctx.site.can_write(key):
            return render_template(
                "permission_denied",
                web.ctx.fullpath,
                f"Permission denied to edit {key}.",
            )

        tag = web.ctx.site.get(key)
        if tag is None:
            raise web.notfound()

        return render_template('type/tag/edit', tag)

    def POST(self, key):
        tag = web.ctx.site.get(key)
        if tag is None:
            raise web.notfound()

        i = web.input(_comment=None)
        formdata = self.process_input(i)
        try:
            if not formdata:
                raise web.badrequest()
            elif "_delete" in i:
                tag = web.ctx.site.new(
                    key, {"key": key, "type": {"key": "/type/delete"}}
                )
                tag._save(comment=i._comment)
                raise safe_seeother(key)
            else:
                tag.update(formdata)
                tag._save(comment=i._comment)
                raise safe_seeother(key)
        except (ClientException, ValidationException) as e:
            add_flash_message('error', str(e))
            return render_template("type/tag/edit", tag)

    def process_input(self, i):
        i = utils.unflatten(i)
        return trim_doc(i)


def setup():
    """Do required setup."""
    pass
