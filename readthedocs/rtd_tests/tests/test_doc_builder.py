import os
import tempfile
from unittest import mock
from unittest.mock import patch

import py
import pytest
import yaml
from django.test import TestCase
from django.test.utils import override_settings
from django_dynamic_fixture import get

from readthedocs.builds.models import Version
from readthedocs.doc_builder.backends.mkdocs import (
    MkdocsHTML,
    SafeDumper,
    yaml_load_safely,
)
from readthedocs.doc_builder.backends.sphinx import (
    BaseSphinx,
    HtmlBuilder,
    HtmlDirBuilder,
    SingleHtmlBuilder,
)
from readthedocs.doc_builder.config import load_yaml_config
from readthedocs.doc_builder.environments import LocalBuildEnvironment
from readthedocs.doc_builder.exceptions import MkDocsYAMLParseError
from readthedocs.doc_builder.python_environments import Virtualenv
from readthedocs.projects.exceptions import ProjectConfigurationError
from readthedocs.projects.models import Feature, Project
from readthedocs.rtd_tests.tests.test_config_integration import create_load


@override_settings(PRODUCTION_DOMAIN="readthedocs.org")
class SphinxBuilderTest(TestCase):
    fixtures = ["test_data", "eric"]

    def setUp(self):
        self.project = Project.objects.get(slug='pip')
        self.version = self.project.versions.first()

        self.build_env = mock.MagicMock()
        self.build_env.project = self.project
        self.build_env.version = self.version
        self.build_env.build = {
            "id": 123,
        }
        self.build_env.api_client = mock.MagicMock()

        BaseSphinx.type = 'base'
        BaseSphinx.sphinx_build_dir = tempfile.mkdtemp()
        BaseSphinx.relative_output_dir = "_readthedocs/"

    @patch('readthedocs.doc_builder.backends.sphinx.BaseSphinx.docs_dir')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_conf_py_path(self, checkout_path, docs_dir):
        """
        Test the conf_py_path that is added to the conf.py file.

        This value is used from the theme and footer
        to build the ``View`` and ``Edit`` on link.
        """
        tmp_dir = tempfile.mkdtemp()
        checkout_path.return_value = tmp_dir
        docs_dir.return_value = tmp_dir
        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        base_sphinx = BaseSphinx(
            build_env=self.build_env,
            python_env=python_env,
        )

        for value, expected in (('conf.py', '/'), ('docs/conf.py', '/docs/')):
            base_sphinx.config_file = os.path.join(
                tmp_dir, value,
            )
            params = base_sphinx.get_config_params()
            self.assertEqual(
                params['conf_py_path'],
                expected,
            )

    @patch('readthedocs.doc_builder.backends.sphinx.BaseSphinx.docs_dir')
    @patch('readthedocs.doc_builder.backends.sphinx.BaseSphinx.get_config_params')
    @patch('readthedocs.doc_builder.backends.sphinx.BaseSphinx.run')
    @patch('readthedocs.builds.models.Version.get_conf_py_path')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_project_without_conf_py(
        self,
        checkout_path,
        get_conf_py_path,
        _,
        get_config_params,
        docs_dir,
    ):
        """
        Test for a project without ``conf.py`` file.

        When this happen, the ``get_conf_py_path`` raises a
        ``ProjectConfigurationError`` which is captured by our own code.
        """
        tmp_dir = tempfile.mkdtemp()
        checkout_path.return_value = tmp_dir
        docs_dir.return_value = tmp_dir
        get_config_params.return_value = {}
        get_conf_py_path.side_effect = ProjectConfigurationError
        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        base_sphinx = BaseSphinx(
            build_env=self.build_env,
            python_env=python_env,
        )
        with pytest.raises(
            ProjectConfigurationError, match=ProjectConfigurationError.NOT_FOUND
        ):
            base_sphinx.append_conf()

    @patch('readthedocs.doc_builder.backends.sphinx.BaseSphinx.docs_dir')
    @patch('readthedocs.doc_builder.backends.sphinx.BaseSphinx.get_config_params')
    @patch('readthedocs.doc_builder.backends.sphinx.BaseSphinx.run')
    @patch('readthedocs.builds.models.Version.get_conf_py_path')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_multiple_conf_py(
        self, checkout_path, get_conf_py_path, _, get_config_params, docs_dir
    ):
        """
        Test for a project with multiple ``conf.py`` files.

        An error should be raised to the user if we can't
        guess the correct conf.py file.
        """

        tmp_docs_dir = py.path.local(tempfile.mkdtemp())
        tmp_docs_dir.join('conf.py').write('')
        tmp_docs_dir.join('test').mkdir().join('conf.py').write('')
        docs_dir.return_value = str(tmp_docs_dir)
        checkout_path.return_value = str(tmp_docs_dir)
        get_config_params.return_value = {}
        get_conf_py_path.side_effect = ProjectConfigurationError
        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        base_sphinx = BaseSphinx(
            build_env=self.build_env,
            python_env=python_env,
        )
        with pytest.raises(ProjectConfigurationError):
            with override_settings(DOCROOT=tmp_docs_dir):
                base_sphinx.append_conf()

    @mock.patch("readthedocs.doc_builder.config.load_config")
    def test_use_sphinx_builders(self, load_config):
        config_data = {"version": 2, "sphinx": {"configuration": "docs/conf.py"}}
        load_config.side_effect = create_load(config_data)
        config = load_yaml_config(self.version)

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=config,
        )
        builder = HtmlBuilder(
            build_env=self.build_env,
            python_env=python_env,
        )
        self.assertEqual(builder.sphinx_builder, "html")

        builder = HtmlDirBuilder(
            build_env=self.build_env,
            python_env=python_env,
        )
        self.assertEqual(builder.sphinx_builder, "dirhtml")

        builder = SingleHtmlBuilder(
            build_env=self.build_env,
            python_env=python_env,
        )
        self.assertEqual(builder.sphinx_builder, "singlehtml")


@override_settings(PRODUCTION_DOMAIN='readthedocs.org')
class MkdocsBuilderTest(TestCase):

    def setUp(self):
        self.project = get(Project, documentation_type='mkdocs', name='mkdocs')
        self.version = get(Version, project=self.project)

        self.build_env = LocalBuildEnvironment(api_client=mock.MagicMock())
        self.build_env.project = self.project
        self.build_env.version = self.version

    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_get_theme_name(self, checkout_path):
        tmpdir = tempfile.mkdtemp()
        checkout_path.return_value = tmpdir
        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        builder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )

        # The default theme is mkdocs but in mkdocs>=1.0, theme is required
        self.assertEqual(builder.get_theme_name({}), 'mkdocs')

        # mkdocs<0.17 syntax
        config = {
            'theme': 'readthedocs',
        }
        self.assertEqual(builder.get_theme_name(config), 'readthedocs')

        # mkdocs>=0.17 syntax
        config = {
            'theme': {
                'name': 'test_theme',
            },
        }
        self.assertEqual(builder.get_theme_name(config), 'test_theme')

        # No theme but just a directory
        config = {
            'theme_dir': '/path/to/mydir',
        }
        self.assertEqual(builder.get_theme_name(config), 'mydir')
        config = {
            'theme_dir': '/path/to/mydir/',
        }
        self.assertEqual(builder.get_theme_name(config), 'mydir')

    @patch('readthedocs.doc_builder.base.BaseBuilder.run')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_get_theme_name_with_feature_flag(self, checkout_path, run):
        tmpdir = tempfile.mkdtemp()
        checkout_path.return_value = tmpdir

        feature = get(
            Feature,
            feature_id=Feature.MKDOCS_THEME_RTD,
        )
        feature.projects.add(self.project)

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        builder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )
        self.assertEqual(builder.get_theme_name({}), 'readthedocs')
        with patch('readthedocs.doc_builder.backends.mkdocs.yaml') as mock_yaml:
            with patch('readthedocs.doc_builder.backends.mkdocs.MkdocsHTML.load_yaml_config') as mock_load_yaml_config:
                mock_load_yaml_config.return_value = {
                    "site_name": self.project.name,
                    "docs_dir": tmpdir,
                }
                with override_settings(DOCROOT=tmpdir):
                    builder.append_conf()

            mock_yaml.dump.assert_called_once_with(
                {
                    'site_name': mock.ANY,
                    'docs_dir': mock.ANY,
                    'extra_javascript': mock.ANY,
                    'extra_css': mock.ANY,
                    'google_analytics': mock.ANY,
                    'theme': 'readthedocs',
                },
                stream=mock.ANY,
                Dumper=SafeDumper,
            )
            mock_yaml.reset_mock()

            config = {
                'theme': 'customtheme',
            }
            self.assertEqual(builder.get_theme_name(config), 'customtheme')
            with patch('readthedocs.doc_builder.backends.mkdocs.MkdocsHTML.load_yaml_config') as mock_load_yaml_config:
                mock_load_yaml_config.return_value = {
                    "site_name": self.project.name,
                    "theme": "customtheme",
                    "docs_dir": tmpdir,
                }
                with override_settings(DOCROOT=tmpdir):
                    builder.append_conf()

            mock_yaml.dump.assert_called_once_with(
                {
                    'site_name': mock.ANY,
                    'docs_dir': mock.ANY,
                    'extra_javascript': mock.ANY,
                    'extra_css': mock.ANY,
                    'google_analytics': mock.ANY,
                    'theme': 'customtheme',
                },
                stream=mock.ANY,
                Dumper=SafeDumper,
            )

    @patch('readthedocs.doc_builder.base.BaseBuilder.run')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_append_conf_existing_yaml_on_root(self, checkout_path, run):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, 'docs'))
        yaml_file = os.path.join(tmpdir, 'mkdocs.yml')
        yaml.safe_dump(
            {
                'site_name': 'mkdocs',
                'google_analytics': ['UA-1234-5', 'mkdocs.org'],
                'docs_dir': 'docs',
            },
            open(yaml_file, 'w'),
        )
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        self.searchbuilder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )
        with override_settings(DOCROOT=tmpdir):
            self.searchbuilder.append_conf()

        run.assert_called_with("cat", "mkdocs.yml", cwd=mock.ANY)

        config = yaml_load_safely(open(yaml_file))
        self.assertEqual(
            config["docs_dir"],
            "docs",
        )
        self.assertEqual(
            config["extra_css"],
            [
                "/_/static/css/badge_only.css",
                "/_/static/css/readthedocs-doc-embed.css",
            ],
        )
        self.assertEqual(
            config["extra_javascript"],
            [
                "readthedocs-data.js",
                "/_/static/core/js/readthedocs-doc-embed.js",
                "/_/static/javascript/readthedocs-analytics.js",
            ],
        )
        self.assertIsNone(
            config["google_analytics"],
        )
        self.assertEqual(
            config["site_name"],
            "mkdocs",
        )

    @patch("readthedocs.doc_builder.base.BaseBuilder.run")
    @patch("readthedocs.projects.models.Project.checkout_path")
    def test_append_conf_mkdocs_07x(self, checkout_path, run):
        get(
            Feature,
            feature_id=Feature.DEFAULT_TO_MKDOCS_0_17_3,
            projects=[self.project],
        )
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, "docs"))
        yaml_file = os.path.join(tmpdir, "mkdocs.yml")
        yaml.safe_dump(
            {
                "site_name": "mkdocs",
                "google_analytics": ["UA-1234-5", "mkdocs.org"],
                "docs_dir": "docs",
            },
            open(yaml_file, "w"),
        )
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        builder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )
        with override_settings(DOCROOT=tmpdir):
            builder.append_conf()

        run.assert_called_with('cat', 'mkdocs.yml', cwd=mock.ANY)

        config = yaml_load_safely(open(yaml_file))
        self.assertEqual(
            config['docs_dir'],
            'docs',
        )
        self.assertEqual(
            config['extra_css'],
            [
                'http://readthedocs.org/static/css/badge_only.css',
                'http://readthedocs.org/static/css/readthedocs-doc-embed.css',
            ],
        )
        self.assertEqual(
            config['extra_javascript'],
            [
                'readthedocs-data.js',
                'http://readthedocs.org/static/core/js/readthedocs-doc-embed.js',
                'http://readthedocs.org/static/javascript/readthedocs-analytics.js',
            ],
        )
        self.assertIsNone(
            config['google_analytics'],
        )
        self.assertEqual(
            config['site_name'],
            'mkdocs',
        )

    @patch('readthedocs.doc_builder.base.BaseBuilder.run')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_append_conf_existing_yaml_on_root_with_invalid_setting(self, checkout_path, run):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, 'docs'))
        yaml_file = os.path.join(tmpdir, 'mkdocs.yml')
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        self.searchbuilder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )

        # We can't use ``@pytest.mark.parametrize`` on a Django test case
        yaml_contents = [
            {"docs_dir": ["docs"]},
            {"extra_css": "a string here"},
            {"extra_javascript": ""},
        ]
        for content in yaml_contents:
            yaml.safe_dump(
                content,
                open(yaml_file, 'w'),
            )
            with self.assertRaises(MkDocsYAMLParseError):
                with override_settings(DOCROOT=tmpdir):
                    self.searchbuilder.append_conf()

    @patch("readthedocs.doc_builder.base.BaseBuilder.run")
    @patch("readthedocs.projects.models.Project.checkout_path")
    def test_append_conf_and_none_values(self, checkout_path, run):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, "docs"))
        yaml_file = os.path.join(tmpdir, "mkdocs.yml")
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        builder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )

        yaml.safe_dump(
            {
                "extra_css": None,
                "extra_javascript": None,
            },
            open(yaml_file, "w"),
        )
        with override_settings(DOCROOT=tmpdir):
            builder.append_conf()
        config = yaml_load_safely(open(yaml_file))

        self.assertEqual(
            config["extra_css"],
            [
                "/_/static/css/badge_only.css",
                "/_/static/css/readthedocs-doc-embed.css",
            ],
        )
        self.assertEqual(
            config["extra_javascript"],
            [
                "readthedocs-data.js",
                "/_/static/core/js/readthedocs-doc-embed.js",
                "/_/static/javascript/readthedocs-analytics.js",
            ],
        )

    @patch('readthedocs.doc_builder.base.BaseBuilder.run')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_dont_override_theme(self, checkout_path, run):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, 'docs'))
        yaml_file = os.path.join(tmpdir, 'mkdocs.yml')
        yaml.safe_dump(
            {
                'theme': 'not-readthedocs',
                'theme_dir': 'not-readthedocs',
                'site_name': 'mkdocs',
                'docs_dir': 'docs',
            },
            open(yaml_file, 'w'),
        )
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        self.searchbuilder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )
        with override_settings(DOCROOT=tmpdir):
            self.searchbuilder.append_conf()

        run.assert_called_with('cat', 'mkdocs.yml', cwd=mock.ANY)

        config = yaml_load_safely(open(yaml_file))
        self.assertEqual(
            config['theme_dir'],
            'not-readthedocs',
        )

    @patch('readthedocs.doc_builder.backends.mkdocs.BaseMkdocs.generate_rtd_data')
    @patch('readthedocs.doc_builder.base.BaseBuilder.run')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_write_js_data_docs_dir(self, checkout_path, run, generate_rtd_data):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, 'docs'))
        yaml_file = os.path.join(tmpdir, 'mkdocs.yml')
        yaml.safe_dump(
            {
                'site_name': 'mkdocs',
                'docs_dir': 'docs',
            },
            open(yaml_file, 'w'),
        )
        checkout_path.return_value = tmpdir
        generate_rtd_data.return_value = ''

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        self.searchbuilder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )
        with override_settings(DOCROOT=tmpdir):
            self.searchbuilder.append_conf()

        generate_rtd_data.assert_called_with(
            docs_dir='docs',
            mkdocs_config=mock.ANY,
        )

    @patch('readthedocs.doc_builder.backends.mkdocs.BaseMkdocs.generate_rtd_data')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_write_js_data_on_invalid_docs_dir(self, checkout_path, generate_rtd_data):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, 'docs'))
        yaml_file = os.path.join(tmpdir, 'mkdocs.yml')
        yaml.safe_dump(
            {
                'site_name': 'mkdocs',
                'google_analytics': ['UA-1234-5', 'mkdocs.org'],
                'docs_dir': 'invalid_docs_dir',
                'extra_css': [
                    'http://readthedocs.org/static/css/badge_only.css'
                ],
                'extra_javascript': ['readthedocs-data.js'],
            },
            open(yaml_file, 'w'),
        )
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        self.searchbuilder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )
        with self.assertRaises(MkDocsYAMLParseError):
            with override_settings(DOCROOT=tmpdir):
                self.searchbuilder.append_conf()

    @patch('readthedocs.doc_builder.base.BaseBuilder.run')
    @patch('readthedocs.projects.models.Project.checkout_path')
    def test_append_conf_existing_yaml_with_extra(self, checkout_path, run):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, 'docs'))
        yaml_file = os.path.join(tmpdir, 'mkdocs.yml')
        yaml.safe_dump(
            {
                "site_name": "mkdocs",
                "google_analytics": ["UA-1234-5", "mkdocs.org"],
                "docs_dir": "docs",
                "extra_css": ["/_/static/css/badge_only.css"],
                "extra_javascript": ["readthedocs-data.js"],
            },
            open(yaml_file, 'w'),
        )
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        self.searchbuilder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )
        with override_settings(DOCROOT=tmpdir):
            self.searchbuilder.append_conf()

        run.assert_called_with('cat', 'mkdocs.yml', cwd=mock.ANY)

        config = yaml_load_safely(open(yaml_file))

        self.assertEqual(
            config['extra_css'],
            [
                "/_/static/css/badge_only.css",
                "/_/static/css/readthedocs-doc-embed.css",
            ],
        )
        self.assertEqual(
            config['extra_javascript'],
            [
                "readthedocs-data.js",
                "/_/static/core/js/readthedocs-doc-embed.js",
                "/_/static/javascript/readthedocs-analytics.js",
            ],
        )

    @patch("readthedocs.projects.models.Project.checkout_path")
    def test_empty_yaml_config(self, checkout_path):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, "docs"))
        yaml_file = os.path.join(tmpdir, "mkdocs.yml")
        yaml.safe_dump(
            "",
            open(yaml_file, "w"),
        )
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        self.searchbuilder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )

        with self.assertRaisesMessage(
            MkDocsYAMLParseError, MkDocsYAMLParseError.EMPTY_CONFIG
        ):
            with override_settings(DOCROOT=tmpdir):
                self.searchbuilder.append_conf()

    @patch("readthedocs.projects.models.Project.checkout_path")
    def test_yaml_config_not_returns_dict(self, checkout_path):
        tmpdir = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmpdir, "docs"))
        yaml_file = os.path.join(tmpdir, "mkdocs.yml")
        yaml.safe_dump(
            "test_string",
            open(yaml_file, "w"),
        )
        checkout_path.return_value = tmpdir

        python_env = Virtualenv(
            version=self.version,
            build_env=self.build_env,
            config=None,
        )
        self.searchbuilder = MkdocsHTML(
            build_env=self.build_env,
            python_env=python_env,
        )

        with self.assertRaisesMessage(
            MkDocsYAMLParseError, MkDocsYAMLParseError.CONFIG_NOT_DICT
        ):
            with override_settings(DOCROOT=tmpdir):
                self.searchbuilder.append_conf()
