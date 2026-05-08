function [File,quitProgram] = getExpInfo_Tek(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 70];       % dialog dimensions
% Options
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmExpInfo = false;
quitProgram = false;
subject = 'Test';
user = 'Last, First';
netID = '';

%% Function
opts.Default = BUTTON_CONFIRM;       % option dedault
while confirmExpInfo == false
    fprintf('Enter experiment information...');

    % Dialog box
    promptInputExpInfo = { ...   % dialog prompts
        'Experiment Name:', ...  % notebook
        'User {\bf(Lastname, Firstname)}:', ...      % name
        'NetID:'};            % netID
    defaultInputExpInfo = {subject,user,netID};      % input defaults
    inputExpInfo = inputdlg( ... % input dialog
        promptInputExpInfo, ...  % input prompts
        'Experiment Information', ...   % input title
        DIMS_DIALOG, ...         % dialog dimensions
        defaultInputExpInfo, ... % input defaults
        opts);                  % dialog options

    % Collect input
    if isempty(inputExpInfo)                  % cancel detected
        fprintf('\nQuitting...');             % quitting
        quitProgram = true;
        return;                                 % exit program
    end
    subject = inputExpInfo{1};         % notebook
    user_raw = inputExpInfo{2};             % user
    if ~contains(user_raw,',')
        user_split = strsplit(user_raw);
        user = [user_split{2} ', ' user_split{1}];
    elseif ~contains(user_raw,', ')
        user_split = strsplit(user_raw,',');
        user = [user_split{1} ', ' user_split{2}];
    else
        user = user_raw;
    end
    netID = inputExpInfo{3};
    if isempty(netID)
        emailAddress = '';
    else
        if contains(netID,'@')
            netID_len = length(netID);
            atSign_idx = strfind(netID,'@');
            netID(atSign_idx:netID_len) = [];
        end
        emailAddress = sprintf('%s@utdallas.edu',netID);
    end

    % Confirm experiment information
    % Format questions
    subject_fix = strrep(subject,'_','\_');
    subject_use = sprintf('Experiment Name: {\\bf%s}',subject_fix); % formatted notebook
    user_use = sprintf('User: {\\bf%s}',user);       	% formatted user
    netID_use = sprintf('NetID: {\\bf%s}',netID);  % formatted subject user
    promptQuestExpInfo = { ...   % question prompts
        subject_use, ...    % inputted notebook
        user_use, ...        % inputted user
        netID_use};
    % Question box
    questExpInfo = questdlg( ...                     % question dialog
        promptQuestExpInfo, ...                      % question prompts
        'Confirm Experiment Information', ...                       % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL, ... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questExpInfo                                 % apply choice
        case BUTTON_CONFIRM                             % check confirmation
            confirmExpInfo = true;                         % confirm info
            fprintf('OK\n'); % info confirmed
        case BUTTON_TRY                                 % try again
            confirmExpInfo = false;                         % trying again
            fprintf('\nTrying agin...\n\n');              % starting over
        case BUTTON_CANCEL                              % quit
            fprintf('\nQuitting...');                 % quitting
            quitProgram = true;
            break;                                     % exit program
        otherwise                                       % cancel
            fprintf('\nQuitting...');                 % quitting
            quitProgram = true;
            break;                                     % exit program
    end
end
if quitProgram
    return;
end

% Store
File.Subject = subject;       % notebook
File.User = user;         % name
File.Email = emailAddress; % email
File.DateTimeCreated = getDateTime();
% Setup email
if ~isempty(emailAddress)
    setNILEmail;
end

end