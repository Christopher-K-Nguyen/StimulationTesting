function [File,quitProgram] = getAnimalExpInfo3(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 70];       % dialog dimensions
% Options

%% Variables
confirmExpInfo = false;
quitProgram = false;
notebook = '1096';
name = 'Last, First';
week = [];
netID = '';
emailAddress = '';
%% Function
while confirmExpInfo == false
    fprintf('Enter experiment information...');
    
    % Dialog box
    titleInputExpInfo = 'Experiment Information';   % dialog title
    promptInputExpInfo = {...   % dialog prompts
        'Notebook {\bf([Book####][Page###][Letter] with NO underscores)}:',...  % notebook
        'Week:',...                                         % week
        'User: {\bf(Lastname, Firstname)}:',...      % name
        'NetID:'};            % netID
    defaultInputExpInfo = {notebook,num2str(week),name,netID};      % input defaults
    opts.Interpreter = 'tex';   % option LaTeX
    inputExpInfo = inputdlg(... % input dialog
        promptInputExpInfo,...  % input prompts
        titleInputExpInfo,...   % input title
        DIMS_DIALOG,...         % dialog dimensions
        defaultInputExpInfo,... % input defaults
        opts);                  % dialog options
        
    % Collect input
    if isempty(inputExpInfo)                  % cancel detected
        fprintf('\nQuitting...');             % quitting
        quitProgram = true;
        return;                                 % exit program
    end
    notebook = inputExpInfo{1};         % notebook
    week = str2double(inputExpInfo{2}); % week
    name_raw = inputExpInfo{3};             % name
    if ~contains(name_raw,',')
        name_split = strsplit(name_raw);
        name = [name_split{2} ', ' name_split{1}];
    elseif ~contains(name_raw,', ')
        name_split = strsplit(name_raw,',');
        name = [name_split{1} ', ' name_split{2}];
    else
        name = name_raw;
    end
    netID = inputExpInfo{4};
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
    titleQuestExpInfo = 'Confirm Experiment Information';
    % Format questions
    notebook_use = sprintf('Notebook: {\\bf%s}',notebook); % formatted notebook
    week_use = sprintf('Week: {\\bf%d}',week); % formatted week
%     name_use = sprintf('Recorded By: {\\bf%s}',name_underscore);       	% formatted name
    name_use = sprintf('Recorded By: {\\bf%s}',name);       	% formatted name
    netID_use = sprintf('NetID: {\\bf%s}',netID);  % formatted subject name
    promptQuestExpInfo = {...   % question prompts
        notebook_use,...    % inputted notebook
        week_use,...        % inputted week
        name_use,...        % inputted name
        netID_use};  % inputted subject name
    opts.Default = BUTTON_CONFIRM;       % option dedault
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
            fprintf('OK.\n'); % info confirmed
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

File.Notebook = notebook;       % notebook
File.Week = week;               % week
File.User = name;         % name

% Setup email
File.Email = emailAddress; % email
if ~isempty(emailAddress)
    setNILEmail;
end

% subjectSelect = File.Subject;	% subject
% testType = File.Parameters.Type;
% if contains(subjectSelect,'A')...
%         && ~strcmpi(testType,'RATE')...
%         && ~strcmpi(testType,'MULTI')...
%         && (week > 5)
%     refElectode = File.ReferenceElectrode.Type;
%     File.ReferenceElectrode.Type = {refElectode,'Ag|AgCl'};
%     prompt = 'Enter 0 for normal potential limit, otherwise 1 for shifted: ';
%     isCorrected = input(prompt);
%     if isCorrected
%         potentialShift = 0.2;
%         File.ReferenceElectrode.Shift = potentialShift;
%         openCircuitPotential = File.ReferenceElectrode.OpenCircuitPotential;
%         lowerPotential = File.ReferenceElectrode.LowerPotential;
%         upperPotential = File.ReferenceElectrode.UpperPotential;
%         openCircuitPotential_new = openCircuitPotential - potentialShift;
%         lowerPotential_new = lowerPotential + potentialShift;
%         upperPotential_new = upperPotential + potentialShift;
%         File.ReferenceElectrode.OpenCircuitPotential = openCircuitPotential_new;
%         File.ReferenceElectrode.LowerPotential = lowerPotential_new;
%         File.ReferenceElectrode.UpperPotential = upperPotential_new;
%     else
%         File.ReferenceElectrode.Shift = 0;
%     end 
% end

end