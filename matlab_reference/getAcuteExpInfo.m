function [name,geomSurfaceArea,emailAddress,quitProgram] = getAcuteExpInfo()
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
confirmExpInfo = 0;
quitProgram = 0;
name = '';
geomSurfaceArea = 0;
emailAddress = '';

%% Function
while confirmExpInfo == 0
    fprintf('Enter experiment information...');
    
    % Dialog box
    titleInputExpInfo = 'Experiment Information';   % dialog title
    promptInputExpInfo = {...                       % dialog prompts
        'Name:',...                                 % name
        'Geometric surface area (\mum^{2}):',...    % geometric surface area
        'Email for notification {\bf(optional)}:'}; % email address
    defaultInputExpInfo = {'','',''};               % input defaults
    inputExpInfo = inputdlg(... % input dialog
        promptInputExpInfo,...  % input prompts
        titleInputExpInfo,...   % input title
        DIMS_DIALOG,...         % dialog dimensions
        defaultInputExpInfo,... % input defaults
        opts);                  % dialog options
        
    % Collect input
    name = inputExpInfo{1};                  % name
    name_underscore = strrep(name,'_','\_');
    geomSurfaceArea = str2double(inputExpInfo{2});  % geometric surface area
    emailAddress = inputExpInfo{3};
    cancelInputExpInfo = isempty(inputExpInfo); % cancel dialog
    if cancelInputExpInfo == 1                  % cancel detected
        fprintf('\nQuitting...');             % quitting
        quitProgram = 1;
        return;                                 % exit program
    end
    % Confirm experiment information
    titleQuestExpInfo = 'Confirm Experiment Information';
    % Format questions
    name_use = sprintf('name: {\\bf%s}',name_underscore);       	% formatted name
    geomSurfaceArea_use = sprintf('Geometric surface area: {\\bf%d \\mum^{2}}',geomSurfaceArea); % formatted geometric surface area
    emailAddress_use = sprintf('Email: {\\bf%s}',emailAddress);  % formatted subject name
    promptQuestExpInfo = {...   % question prompts
        name_use,...            % inputted name
        geomSurfaceArea_use,... % inputted geomtric surface area
        emailAddress_use};      % inputted subject name
    % Question box
    questExpInfo = questdlg(...                     % question dialog
        promptQuestExpInfo,...                      % question prompts
        titleQuestExpInfo,...                       % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questExpInfo                                 % apply choice
        case BUTTON_CONFIRM                             % check confirmation
            confirmExpInfo = 1;                         % confirm info
            fprintf('OK.\n'); % info confirmed
        case BUTTON_TRY                                 % try again
            confirmExpInfo = 0;                         % trying again
            fprintf('\nTrying agin...\n\n');              % starting over
        case BUTTON_CANCEL                              % quit
            fprintf('\nQuitting...');                 % quitting
            quitProgram = 1;
            return;                                     % exit program
        otherwise                                       % cancel
            fprintf('\nQuitting...');                 % quitting
            quitProgram = 1;
            return;                                     % exit program
    end
end

end