function [File,quitProgram] = getAnimalExpInfo2(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 60];       % dialog dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmExpInfo = false;
quitProgram = false;
notebook = '';
name = '';
week = 0;
emailAddress = '';

%% Function
while confirmExpInfo == false
    fprintf('Enter experiment information...');
    
    % Dialog box
    titleInputExpInfo = 'Experiment Information';   % dialog title
    promptInputExpInfo = {...   % dialog prompts
        'Notebook {\bf([Book####][Page###][Letter])}:',...  % notebook
        'Week:',...                                         % week
        'Recorded By: {\bf(Lastname, Firstname)}:',...      % name
        'UTD Email {\bf(netid@utdallas.edu)}:'};            % email address
    defaultInputExpInfo = {'1096','','Last, First','@utdallas.edu'};      % input defaults
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
    name = inputExpInfo{3};             % name
%     name_underscore = strrep(name,'_','\_');
    emailAddress = inputExpInfo{4};
    atSign = strfind(emailAddress,'@');
    if atSign == 1
        emailAddress = '';
    end
    
    % Confirm experiment information
    titleQuestExpInfo = 'Confirm Experiment Information';
    % Format questions
    notebook_use = sprintf('Notebook: {\\bf%s}',notebook); % formatted notebook
    week_use = sprintf('Week: {\\bf%d}',week); % formatted week
%     name_use = sprintf('Recorded By: {\\bf%s}',name_underscore);       	% formatted name
    name_use = sprintf('Recorded By: {\\bf%s}',name);       	% formatted name
    emailAddress_use = sprintf('Email: {\\bf%s}',emailAddress);  % formatted subject name
    promptQuestExpInfo = {...   % question prompts
        notebook_use,...    % inputted notebook
        week_use,...        % inputted week
        name_use,...        % inputted name
        emailAddress_use};  % inputted subject name
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
            return;                                     % exit program
        otherwise                                       % cancel
            fprintf('\nQuitting...');                 % quitting
            quitProgram = true;
            return;                                     % exit program
    end
end

File.Notebook = notebook;       % notebook
File.Week = week;               % week
File.RecordedBy = name;         % name

% Setup email
File.Email = emailAddress; % email
if ~isempty(emailAddress)
    setNILEmail;
end

subjectSelect = File.Subject;	% subject
if contains(subjectSelect,'A') && week > 5
    potentialShift = 0.2;
    openCircuitPotential = File.ReferenceElectrode.OpenCircuitPotential;
    lowerPotential = File.ReferenceElectrode.LowerPotential;
    upperPotential = File.ReferenceElectrode.UpperPotential;
    openCircuitPotential_new = openCircuitPotential - potentialShift;
    lowerPotential_new = lowerPotential + potentialShift;
    upperPotential_new = upperPotential + potentialShift;
    File.ReferenceElectrode.Type = 'Ag|AgCl';
    File.ReferenceElectrode.OpenCircuitPotential = openCircuitPotential_new;
    File.ReferenceElectrode.LowerPotential = lowerPotential_new;
    File.ReferenceElectrode.UpperPotential = upperPotential_new;
end

end