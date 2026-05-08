function [...
    notebook,...
    serial,...
    name,emailAddress,...
    pulsePeriod,pulsePause,pulseStop,...
    percentageStim,...
    quitProgram]...
    = getExpInfo2()
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 70];       % dialog dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmExpInfo = false;
quitProgram = false;
notebook = '';
serial = '';
name = '';
emailAddress = '';
percentageStim = 100;

%% Function
while confirmExpInfo == false
    fprintf('Enter experiment information...');
    
    % Dialog box
    titleInputExpInfo = 'Experiment Information';   % dialog title
    promptInputExpInfo = {...   % dialog prompts
        'Notebook {\bf([Book####][Page###][Letter])}:',...  % notebook
        'Serial Number {\bf(SN: ####-######)}:',...           % serial number
        'Recorded By: {\bf(Lastname, Firstname)}:',...      % name
        'UTD Email {\bf(netid@utdallas.edu)}:',...            % email address
        'Periodic number of pulses for pulsing {\bf(use "e" for exponent)}:',...
        'Periodic nuumber of pulses to pause pulsing {\bf(use "e" for exponent)}:',...
        'Target number of pulses to stop at {\bf(use "e" for exponent)}:',...
        'Percentage of max charge injection to stimulate {\bf(integer)}:'};
    defaultInputExpInfo = {...      % input defaults
        '1071###A','####-######',...
        'Last, First','@utdallas.edu',...
        '1e6','1e7','1e8',...
        '100'};
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
    serial = inputExpInfo{2};           % serial
    name = inputExpInfo{3};             % name
%     name_underscore = strrep(name,'_','\_');
    emailAddress = inputExpInfo{4};
    pulsePeriod_char = inputExpInfo{5};
    pulsePeriod = str2double(pulsePeriod_char);
    pulsePause_char = inputExpInfo{6};
    pulsePause = str2double(pulsePause_char);
    pulseStop_char = inputExpInfo{7};
    pulseStop = str2double(pulsePause_char);
    percentageStim = str2double(inputExpInfo{8});

    % Confirm experiment information
    titleQuestExpInfo = 'Confirm Experiment Information';
    % Format questions
    notebook_use = sprintf('Notebook: {\\bf%s}',notebook); % formatted notebook
    serial_use = sprintf('Serial Number: {\\bf%s}',serial); % formatted serial number
%     name_use = sprintf('Recorded By: {\\bf%s}',name_underscore);       	% formatted name
    name_use = sprintf('Recorded By: {\\bf%s}',name);       	% formatted name
    emailAddress_use = sprintf('Email: {\\bf%s}',emailAddress);  % formatted subject name
    pulsePeriod_use = sprintf('Periodic number of pulses for pulsing: {\\bf%s pulses}',pulsePeriod_char);
    pulsePause_use = sprintf('Periodic nuumber of pulses to pause pulsing: {\\bf%s pulses}',pulsePause_char);
    pulseStop_use = sprintf('Target number of pulses to stop at: {\\bf%s pulses}',pulseStop_char);
    percentageStim_use = sprintf('Percentage of max charge injection to stimulate: {\\bf%g%%}',percentageStim);
    promptQuestExpInfo = {...   % question prompts
        notebook_use,...        % inputted notebook
        serial_use,...          % inputted serial number
        name_use,...            % inputted name
        emailAddress_use,...    % inputted subject name
        pulsePause_use,...
        pulsePeriod_use,...
        pulseStop_use,...
        percentageStim_use};
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

end