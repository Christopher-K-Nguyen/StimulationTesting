function [File,isQuit] = getExperimentInfo(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 70];       % dialog dimensions
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmExpInfo = false;
isQuit = false;
defaultNotebook = '';
defaultSubject = '';
defaultID = '';
defaultName = '';
defaultNetID = '';
defaultPhone = '';
defaultCarrier = '';

%% Function
while ~confirmExpInfo
    fprintf('Enter experiment information...');

    % Dialog box
    titleInputExpInfo = 'Experiment Information';   % dialog title
    promptInputExpInfo = {...                       % dialog prompts
        'Notebook {\bf([Notebook####][Page###][Letter])}:', ...
        'Subject name:', ...                        % subject name
        'Optional ID/Serial/Lot:', ...
        'Optional name (Lastname, Firstname):', ...
        'Optional NetID {\bf(receive email message)}:', ...
        'Optional phone (+1) {\bf(receive text message)}', ...
        'Optional phone carrier (USA) {\bf(required for text message)}'};
    defaultInputExpInfo = {defaultNotebook,defaultSubject,defaultID, ...
        defaultName,defaultNetID,defaultPhone,defaultCarrier};  % input defaults
    inputExpInfo = inputdlg(... % input dialog
        promptInputExpInfo,...  % input prompts
        titleInputExpInfo,...   % input title
        DIMS_DIALOG,...         % dialog dimensions
        defaultInputExpInfo,... % input defaults
        opts);                  % dialog options

    % Collect input
    if isempty(inputExpInfo) % cancel dialog
        fprintf('\nQuitting...');             % quitting
        isQuit = true;
        break;                                 % exit program
    end
    notebook = inputExpInfo{1};                  % subject name
    notebook_fix = strrep(notebook,'_','\_');
    defaultNotebook = notebook;
    subject = inputExpInfo{2};                  % subject
    subject_fix = strrep(subject,'_','\_');
    defaultSubject = subject;
    idNum = inputExpInfo{3};                  % serial
    idNum_fix = strrep(idNum,'_','\_');
    defaultID = idNum;
    name = inputExpInfo{4};
    defaultName = name;
    name_fix = name;
    space_idx = strfind(name,' ');
    if ~contains(name,',')
        if ~isempty(space_idx)
            name_len = length(name);
            firstname = name(1:space_idx-1);
            lastname = name(space_idx+1:name_len);
            name_fix = [lastname ', ' firstname];
        end
    else
        if isempty(space_idx)
            name_fix = insertAfter(name,',',' ');
        end
    end
    name_fix = strrep(name_fix,'  ',' ');
    netID = inputExpInfo{5};
    atSign_idx = strfind(netID,'@');
    if ~isempty(atSign_idx)
        netID_len = length(netID);
        netID(atSign_idx:netID_len) = [];
    end
    defaultNetID = netID;
    if isempty(netID)
        email = '';
    else
        email = [netID '@utdallas.edu'];
    end
    defaultPhone = inputExpInfo{6};
    phone = defaultPhone;
    carrier_raw = inputExpInfo{7};
    if ~isempty(carrier_raw)
        if contains2(carrier_raw,{'mobile','t-','tmo'})
            carrier = 'tmobile';
        elseif contains2(carrier_raw,'ver')
            carrier = 'verizon';
        elseif contains2(carrier_raw,'spr')
            carrier = 'sprint';
        elseif contains(carrier_raw,{'at','&'})
            carrier = 'att';
        elseif contains(carrier_raw,'cri')
            carrier = 'cricket';
        elseif contains(carrier_raw,'vir')
            carrier = 'virgin';
        end
    else
        carrier = '';
    end
    defaultCarrier = carrier;

    % Confirm experiment information
    titleQuestExpInfo = 'Confirm Experiment Information';
    % Format questions
    notebook_use = sprintf('Notebook: {\\bf%s}',notebook_fix);   % formatted notebook
    subject_use = sprintf('Subject name: {\\bf%s}',subject_fix);% formatted subject
    idNum_use = sprintf('ID/Serial/Lot: {\\bf%s}',idNum_fix);% formatted serial
    name_use = sprintf('Name: {\\bf%s}',name_fix);
    netID_use = sprintf('NetID: {\\bf%s}',netID);
    phone_use = sprintf('Phone: {\\bf%s}',phone);
    carrier_use = sprintf('Carrier: {\\bf%s}',carrier);
    promptQuestExpInfo = {...   % question prompts
        notebook_use, ...       % inputted notebook
        subject_use, ...        % inputted subject
        idNum_use, ...          % inputted ID
        name_use, ...
        netID_use, ...
        phone_use, ...
        carrier_use};
    % Question box
    questExpInfo = questdlg(...                     % question dialog
        promptQuestExpInfo,...                      % question prompts
        titleQuestExpInfo,...                       % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questExpInfo                                 % apply choice
        case BUTTON_CONFIRM                             % check confirmation
            confirmExpInfo = true;                         % confirm info
            fprintf('OK\n'); % info confirmed
        case BUTTON_TRY                                 % try again
            confirmExpInfo = false;                         % trying again
            fprintf('Trying agin...\n\n');              % starting over
        case BUTTON_CANCEL                              % quit
            fprintf('\nQuitting...');                 % quitting
            isQuit = true;
            break;                                     % exit program
        otherwise                                       % cancel
            fprintf('\nQuitting...');                 % quitting
            isQuit = true;
            break;                                     % exit program
    end
end
if isQuit
    return;
end

%% Store
File.Notebook = notebook;
File.Subject = subject;
File.ID = idNum;
File.User.Name = name_fix;
File.User.Email = email;
File.User.Phone = phone;
File.User.Carrier = carrier;
% Setup email
if ~isempty(email) || (~isempty(phone) && ~isempty(carrier))
    setNILEmail;
end

%% Folder Path
[File,isQuit] = getSaveFolder(File);
if isQuit
    fprintf('OK\n\n');
    return;
end
fprintf('\n');


%% Experiment Type
%{



%}

end