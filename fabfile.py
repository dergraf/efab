#!/usr/bin/env python
# -*- coding: utf-8 -*-
from os.path import join
import random
from datetime import datetime
from fabric.api import env, puts, cd, hide, task
from fabric.operations import sudo, local, settings, prompt
from fabric.colors import _wrap_with
from fabric.contrib.files import upload_template

green_bg = _wrap_with('42')
red_bg = _wrap_with('41')

@task
def www():
    env.project_name = 'efab'
    env.repository = 'https://github.com/dergraf/efab.git'
    env.hosts = ['tambur.io']
    env.additional_packages = []
    env.project_user = "efab"
    env.project_user_home = join('/opt', env.project_user)
    env.projects_path = join(env.project_user_home, 'projects')
    env.code_root = join(env.projects_path, env.project_name)
    env.erl_node_name = env.project_name
    env.erl_host_name = '127.0.0.1'
    env.erl_cookie = 'mycookie'

@task
def setup():
    puts(green_bg('Start setup...'))
    start_time = datetime.now()

    _verify_sudo()
    _install_dependencies()
    _create_user()
    _setup_directories()
    _git_clone()
    (_version, tag) = _get_git_tag()
    _generate_release(tag)

    end_time = datetime.now()
    finish_message = '[%s] Correctly finished in %i seconds' % \
    (green_bg(end_time.strftime('%H:%M:%S')), (end_time - start_time).seconds)
    puts(finish_message)

@task
def deploy_major_release():
    version, tag = _get_git_tag()
    new_tag = 'v%s.0.0' % (version[0] + 1)
    _deploy_release(tag, new_tag)

@task
def deploy_minor_release():
    version, tag = _get_git_tag()
    new_tag = 'v%s.%s.0' % (version[0], version[1] + 1)
    _deploy_release(tag, new_tag)

@task
def deploy_bugfix_release():
    version, tag = _get_git_tag()
    new_tag = 'v%s.%s.%s' % (version[0], version[1], version[2] + 1)
    _deploy_release(tag, new_tag)

def _deploy_release( tag, new_tag):
    tag_message = prompt("Please enter a tagging message.")
    local("git tag -a %s -m '%s'" % (new_tag, tag_message))
    local("git push --tags")
    _git_pull()
    _upgrade_release(tag, new_tag)

def _get_git_tag():
    local('git checkout master')
    tag = local('git describe --abbrev=0 --tags', capture=True)
    if tag.startswith('v'): #v1.2.3+identifier.1
        version = tag.split('+')[0][1:].split('.')
        return ([int(v) for v in version], tag)
    else:
        return ([0, 0, 0], 'v0.0.0')

def _install_dependencies():
    ''' Ensure those Debian/Ubuntu packages are installed '''
    packages = [
        "build-essential",
        "erlang",
    ]
    sudo("apt-get update")
    sudo("apt-get -y install %s" % " ".join(packages))
    if "additional_packages" in env and env.additional_packages:
        sudo("apt-get -y install %s" % " ".join(env.additional_packages))

def _verify_sudo():
    ''' we just check if the user is sudoers '''
    sudo('cd .')

def _create_user():
    with settings(hide('running', 'stdout', 'stderr', 'warnings'), warn_only=True):
        res = sudo('useradd -d %(project_user_home)s -m -r %(project_user)s' % env)
    if 'already exists' in res:
        puts('User \'%(project_user)s\' already exists, will not be changed.' % env)
        return
    #  set password
    sudo('passwd %(project_user)s' % env)

def _setup_directories():
    sudo('mkdir -p %(projects_path)s' % env)

def _git_clone():
    sudo('git clone %s %s' % (env.repository, env.code_root))

def _git_pull():
    with cd(env.code_root):
        sudo('git pull')

def _generate_release(tag):
    _upload_vm_vars()
    _upload_reltool_config(tag)
    with cd(env.code_root):
        sudo('./rebar get-deps')
        sudo('./rebar compile generate')
        sudo('mv rel/%s rel/%s_%s' % (env.project_name, env.project_name, tag))
        sudo('rm -f active_release')
        sudo('ln -s rel/%s_%s active_release' % (env.project_name, tag))

def _upload_vm_vars():
    template = 'rel/vm.args.template'
    path_to_vm_args = join(env.code_root, 'rel', 'files', 'vm.args')
    upload_template(template, path_to_vm_args, context=env, backup=False, use_sudo=True)

def _upload_reltool_config(tag):
    template = 'rel/reltool.config.template'
    path_to_reltool_config = join(env.code_root, 'rel', 'reltool.config')
    reltool_env = {'tag' : tag}
    upload_template(template, path_to_reltool_config, context=reltool_env, backup=False, use_sudo=True)

def _upgrade_release(current_tag, new_tag):
    _upload_vm_vars()
    _upload_reltool_config(new_tag)
    with cd(env.code_root):
        sudo('./rebar get-deps')
        sudo('./rebar compile generate')
        sudo('./rebar generate-appups previous_release=%s_%s' % (env.project_name, current_tag))
        sudo('./rebar generate-upgrade previous_release=%s_%s' % (env.project_name, current_tag))
        sudo('mkdir -p active_release/releases')
        sudo('mv rel/%s_%s.tar.gz active_release/releases/' % (env.project_name, new_tag))
        _remote_call("release_handler", "unpack_release", "%s_%s" % (env.project_name, new_tag))
        _remote_call("release_handler", "install_release", new_tag)
        _remote_call("release_handler", "make_permanent", new_tag)
        sudo('mv rel/%s rel/%s_%s' % (env.project_name, env.project_name, new_tag))

def _get_node_name():
    return '%s@%s' % (env.erl_node_name, env.erl_host_name)

@task
def _remote_node_available():
    cmd = """erl -name %s -hidden -setcookie %s \
            -noshell -noinput -eval \
            'MainNode = list_to_atom(\"%s\"), \
            io:format(\"~p\", [net_adm:ping(MainNode)])' \
            -s erlang halt
            """ % (_random_node_name(), env.erl_cookie, _get_node_name())

    return sudo(cmd) == 'pong'

@task
def which_release():
    if _remote_node_available():
        cmd = """erl -name %s -hidden -setcookie %s \
                -noshell -noinput -eval \
                'MainNode = list_to_atom(\"%s\"), \
                io:format(\"~p\", [rpc:call(MainNode, release_handler, which_releases, [permanent])])' \
                -s erlang halt
                """ % (_random_node_name(), env.erl_cookie, _get_node_name())
        sudo(cmd)

def _remote_call(module, function, single_string_arg):
    if _remote_node_available():
        cmd = """erl -name %s -hidden -setcookie %s \
                -noshell -noinput -eval \
                'MainNode = list_to_atom(\"%s\"), \
                io:format(\"~p\", [rpc:call(MainNode, %s, %s, [\"%s\"])])' \
                -s erlang halt
                """ % (_random_node_name(), env.erl_cookie, _get_node_name(), module, function, single_string_arg)
        return sudo(cmd) == 'ok'

def _remote_eval(eval_string):
    if _remote_node_available():
        cmd = """erl -name %s -hidden -setcookie %s \
                -noshell -noinput -eval \
                'MainNode = list_to_atom(\"%s\"), \
                {ok, Scanned, _} = erl_scan:string (\"%s\"), \
                {ok, Parsed} = erl_parse:parse_exprs(Scanned), \
                case rpc:call(MainNode, erl_eval, exprs, [Parsed, []]) of \
                    {value, _, _} -> \
                        io:format("ok", []); \
                    _ -> \
                        io:format("error", []) \
                end' \
                -s erlang halt
                """ % (_random_node_name(), env.erl_cookie, _get_node_name(), eval_string)

        return sudo(cmd) == 'ok'

def _random_node_name():
    return 'erlrctmp_' + ''.join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for x in range(8)) + '@%s' % env.erl_host_name
