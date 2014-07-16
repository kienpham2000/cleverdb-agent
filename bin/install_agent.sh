#!/bin/bash
# CleverDb agent install script.
# copied from: https://raw.githubusercontent.com/DataDog/dd-agent/master/packaging/datadog-agent/source/install_agent.sh
set -e
gist_request=/tmp/agent-gist-request.tmp
gist_response=/tmp/agent-gist-response.tmp
yum_repo="http://yum.cleverdb.io"
apt_repo="http://apt.cleverdb.io"
apt_key_repo="hkp://apt.cleverdb.io:80"
app_name=cleverdb-agent
app_path=/opt/sendgridlabs/cleverdb-agent
app_config=$app_path/$app_name.conf
app_url="http://cleverdb.io"
repo_url="https://github.com/sendgridlabs/cleverdb-agent"
install_log="$app_name-install.log"

# SSH detection
has_ssh=$(which ssh || echo "no")
if [ $has_ssh = "no" ]; then
    printf "\033[31mSSH is required to install $app_name.\033[0m\n"
    exit 1;
fi

# OS/Distro detection
if [ -f /etc/debian_version ]; then
    OS=Debian
elif [ -f /etc/redhat-release ]; then
    # Just mark as RedHat and we'll use Python version detection
    # to know what to install
    OS=RedHat
elif [ -f /etc/lsb-release ]; then
    . /etc/lsb-release
    OS=$DISTRIB_ID
else
    OS=$(uname -s)
fi

if [ $OS = "Darwin" ]; then
    printf "\033[31mMac OS is currently not supported.\033[0m\n"
    exit 1;
fi

# Python detection
has_python=$(which python || echo "no")
if [ $has_python = "no" ]; then
    printf "\033[31mPython is required to install $app_name.\033[0m\n"
    exit 1;
fi

# Python version detection
PY_VERSION=$(python -c 'import sys; print "%d.%d" % (sys.version_info[0], sys.version_info[1])')
if [ $PY_VERSION = "2.4" -o $PY_VERSION = "2.5" ]; then
    DDBASE=true
else
    DDBASE=false
fi

# Root user detection
if [ $(echo "$UID") = "0" ]; then
    sudo_cmd=''
else
    sudo_cmd='sudo'
fi

if [ $(which curl) ]; then
    dl_cmd="curl -f"
else
    dl_cmd="wget --quiet"
fi

# Set up a named pipe for logging
npipe=/tmp/$$.tmp
mknod $npipe p

# Log all output to a log for error checking
tee <$npipe $install_log &
exec 1>&-
exec 1>$npipe 2>&1
trap "rm -f $npipe" EXIT

function on_error() {
    printf "\033[31m
It looks like you hit an issue when trying to install $app_name.

Troubleshooting and basic usage information for $app_name are available at:

    $app_url

If you're still having problems, please send an email to support@cleverdb.io
with the contents of $install_log and we'll do our very best to help you
solve your problem.\n\033[0m\n"
}
trap on_error ERR

if [ -n "$CD_API_KEY" ]; then
    apikey=$CD_API_KEY
fi

if [ ! $apikey ]; then
    printf "\033[31m
API key not available in CD_API_KEY environment variable.

Example usage: CD_API_KEY=sample_api_key ${BASH_SOURCE[0]}\n\033[0m\n"
    exit 1;
fi

# Install the necessary package sources
if [ $OS = "RedHat" ]; then
    echo -e "\033[34m\n* Installing YUM sources\n\033[0m"
    $sudo_cmd sh -c "echo -e '[datadog]\nname = SendGrid Labs.\nbaseurl = $yum_repo/rpm/\nenabled=1\ngpgcheck=0\npriority=1' > /etc/yum.repos.d/$app_name.repo"

    printf "\033[34m* Installing the $app_name package\n\033[0m\n"

    if $DDBASE; then
        $sudo_cmd yum -y install cleverdb-agent-base
    else
        $sudo_cmd yum -y install cleverdb-agent
    fi
elif [ $OS = "Debian" -o $OS = "Ubuntu" ]; then
    printf "\033[34m\n* Installing APT package sources\n\033[0m\n"
    $sudo_cmd sh -c "echo 'deb $apt_repo unstable main' > /etc/apt/sources.list.d/$app_name.list"
    $sudo_cmd apt-key adv --recv-keys --keyserver $apt_key_repo C7A7DA52

    printf "\033[34m\n* Installing the $app_name package\n\033[0m\n"
    $sudo_cmd apt-get update
    if $DDBASE; then
        $sudo_cmd apt-get install -y --force-yes $app_name-base
    else
        $sudo_cmd apt-get install -y --force-yes $app_name
    fi
else
    printf "\033[31m
Your OS or distribution is not supported by this install script.
Please follow the instructions on $app_name setup page:

    http://cleverdb.io\n\033[0m\n"
    exit;
fi

printf "\033[34m\n* Adding your API key to $app_name configuration: $config_file\n\033[0m\n"

if $DDBASE; then
    $sudo_cmd sh -c "sed 's/api_key:.*/api_key: $apikey/' /etc/cleverdb-agent/datadog.conf.example | sed 's/# dogstatsd_target :.*/dogstatsd_target: https:\/\/app.datadoghq.com/' > /etc/dd-agent/datadog.conf"
else
    $sudo_cmd sh -c "sed 's/api_key:.*/api_key: $apikey/' /etc/cleverdb-agent/datadog.conf.example > /etc/dd-agent/datadog.conf"
fi

printf "\033[34m* Starting the Agent...\n\033[0m\n"
$sudo_cmd /etc/init.d/cleverdb-agent restart

# Datadog "base" installs don't have a forwarder, so we can't use the same
# check for the initial payload being sent.
if $DDBASE; then
printf "\033[32m
Your Agent has started up for the first time and is submitting metrics to
Datadog. You should see your Agent show up in Datadog shortly at:

    https://app.datadoghq.com/infrastructure\033[0m

If you ever want to stop the Agent, run:

    sudo /etc/init.d/cleverdb-agent stop

And to run it again run:

    sudo /etc/init.d/cleverdb-agent start
"
exit;
fi

# Wait for metrics to be submitted by the forwarder
printf "\033[32m
Your Agent has started up for the first time. We're currently verifying that
data is being submitted. You should see your Agent show up in Datadog shortly
at:

    https://app.datadoghq.com/infrastructure\033[0m

Waiting for metrics..."

c=0
while [ "$c" -lt "30" ]; do
    sleep 1
    echo -n "."
    c=$(($c+1))
done

$dl_cmd http://127.0.0.1:17123/status?threshold=0 > /dev/null 2>&1
success=$?
while [ "$success" -gt "0" ]; do
    sleep 1
    echo -n "."
    $dl_cmd http://127.0.0.1:17123/status?threshold=0 > /dev/null 2>&1
    success=$?
done

# Metrics are submitted, echo some instructions and exit
printf "\033[32m

Your Agent is running and functioning properly. It will continue to run in the
background and submit metrics to Datadog.

If you ever want to stop the Agent, run:

    sudo /etc/init.d/datadog-agent stop

And to run it again run:

    sudo /etc/init.d/datadog-agent start

\033[0m"