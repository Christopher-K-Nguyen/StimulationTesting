function [multiChannel,singleChannel,voltageMonScale,currentMonScale,quitProgram] = getPhysSetup()
%% Constants
YES = 1;
NO = 0;
% Buttons
BUTTON_MULTI = 'Multi';	% omnetics button
BUTTON_SINGLE = 'Single';	% pinout button
BUTTON_YES = 'Yes';
BUTTON_NO = 'No';
BUTTON_OK = 'OK';
BUTTON_QUIT = 'Quit';
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Titles
TITLE_ERROR = 'ERROR';
TITLE_NOTICE = 'NOTICE';
TITLE_CHANNEL_CONNECTION = 'Channel Connection';
TITLE_SINGLE_CHANNEL = 'Confirm Single Channel';
TITLE_QUEST_CHECK = 'Check';
% Dimensions
DIMS_DIALOG = [1 70];       % dialog dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmPhysSetup = 0;       % initialize setup
confirmSingleChannel = 0;
confirmMonitorScale = 0;
quitProgram = 0;
multiChannel = 0;
singleChannel = 0;
voltageMonScale = 0;
currentMonScale = 0;

%% Function
while confirmPhysSetup == NO         	% check setup
    % Notice
    prompt1 = 'This code ONLY works for:';
    prompt2 = '\bullet 1 PlexStim stimulator';
    prompt3 = '\bullet 1 Tektronix oscilloscope via USB';

    notice = questdlg({prompt1,prompt2,prompt3},TITLE_NOTICE,BUTTON_OK,BUTTON_QUIT,opts);
    switch notice
        case BUTTON_OK
        case BUTTON_QUIT                % quit
            fprintf('Quitting...'); % quitting
            quitProgram = 1;
            return;                     % exit program
        otherwise                       % cancel
            fprintf('Quitting...'); % quitting
            quitProgram = 1;
            return;                     % exit program
    end

    % Check
    fprintf('Setup connected to...');
    promptQuestCheckConnect = {'Is the setup connected to multi- or single-channel?'};
    questCheckConnect = questdlg(...                    % question dialog
        promptQuestCheckConnect,...                     % question prompts
        TITLE_QUEST_CHECK,...                           % question litle
        BUTTON_MULTI,BUTTON_SINGLE,BUTTON_QUIT,...   % buttons
        opts);                                          % dialog options
    % Confirmation
    switch questCheckConnect                        % apply choice
        case BUTTON_MULTI                        % connect to multi-channel
            multiChannel = 1;                       % connecting to multi-channel
            fprintf('multi-channels.\n');   % connected to multi-channel
        case BUTTON_SINGLE                          % connect to single-channel
            multiChannel = 0;                       % connecting to single-channel
            fprintf('single-channel.\n');   % connected to single-channel
        case BUTTON_QUIT                    % quit
            fprintf('\nQuitting...');     % quitting
            quitProgram = 1;
            return;                         % exit program
        otherwise                           % cancel
            fprintf('\nQuitting...');     % quitting
            quitProgram = 1;
            return;                         % exit program
    end
    
    if multiChannel == NO
        while confirmSingleChannel == NO
            fprintf('Connecting to PlexStim channel...');
            promptSingleChannel = 'Enter connected PlexStim channel ({\bfdoes not change}):';
            defaultSingleCh = {'1'};
            inputSingleChannel = inputdlg(...
                promptSingleChannel,...
                TITLE_CHANNEL_CONNECTION,...
                DIMS_DIALOG,...
                defaultSingleCh,...
                opts);
            % Collect input
            cancelSingleChannel = isempty(inputTestParam);  % cancel dialog
            if cancelSingleChannel == YES                     % cancel detected
                fprintf('\nQuitting...\n\n');             % quitting
                quitProgram = 1;
                return;                                 % exit program
            end
            singleChannel_cell = inputSingleChannel{1};
            singleChannel = str2double(singleChannel_cell);

            % Question box
            promptQuestSingleCh = sprintf('Connected PlexStim channel: {\\bf%d}',singleChannel);
            questSingleCh = questdlg(...                    % question dialog
                promptQuestSingleCh,...                     % question prompt
                TITLE_SINGLE_CHANNEL,...                    % question title
                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
                opts);
                % Confirmation
            switch questSingleCh                                % apply choice
                case BUTTON_CONFIRM                             % check confirmation
                    confirmSingleChannel = 1;               	% confirm parameters
                    fprintf('%d\n',singleChannel);
                case BUTTON_TRY                     % try again
                    fprintf('Trying again...\n\n'); % starting over
                case BUTTON_CANCEL                  % quit
                    fprintf('\nQuitting...');     % quitting
                    quitProgram = 1;
                    return;                         % exit program
                otherwise                           % cancel
                    fprintf('\nQuitting...');     % quitting
                    quitProgram = 1;
                    return;                         % exit program
            end
        end
    end
    
    while confirmMonitorScale == NO
        fprintf('Enter monitor scaling...');
        % Dialog box
        titleInputStimParam = 'Monitor Scaling'; % input title
        promptInputStimParam = {...                   	% input prompts
            'Voltage monitor scaling (V/V) {\bf(default 0.25, NIL 1)}:',... % voltage monitor scaling (V/V)
            'Current monitor scaling (mV/\muA) {\bf(default 2.5, NIL 1)}:'};% current monitor scaling (mV/uA)
        defaultInputStimParam = {'1','1'};% input defaults
        inputMonitorScaling = inputdlg(...	% input dialog
            promptInputStimParam,...    % input prompts
            titleInputStimParam,...     % input title
            DIMS_DIALOG,...             % dialog dimensions
            defaultInputStimParam,...   % input defaults
            opts);                      % dialog options
        cancelMonitorScaling = isempty(inputMonitorScaling);  % cancel dialog
            if cancelMonitorScaling == YES                     % cancel detected
                fprintf('\nQuitting...\n\n');             % quitting
                quitProgram = 1;
                return;                                 % exit program
            end
        
        % Collect input
        voltageMonScale_cell = inputMonitorScaling{1};           % voltage monitor scale (cell)
        voltageMonScale = str2double(voltageMonScale_cell); % voltage monitor scale (double)
        currentMonScale_cell = inputMonitorScaling{2};           % current monitor scale (cell)
        currentMonScale = str2double(currentMonScale_cell); % current monitor scale (double)        

        % Confirm stimulation parameters
        titleQuestMonitorScale = 'Confirm Montior Scaling';
        % Format questions
        voltageMonScale_use = sprintf('Voltage monitor scaling: {\\bf%.2g V/V}',voltageMonScale);       % formatted voltage monitor scale
        currentMonScale_use = sprintf('Current monitor scaling: {\\bf%.2g mV/\\muA}',currentMonScale);  % formatted current monitor scale
        promptQuestMonitorScale = {...	% dialog questions
            voltageMonScale_use,... % formatted voltage monitor scale
            currentMonScale_use};   % formatted current monitor scale

        % Question box
        questMonitorScale = questdlg(...                   % question dialog
            promptQuestMonitorScale,...                    % question prompts
            titleQuestMonitorScale,...                   	% question title
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);                                      % dialog options
        % Confirmation
        switch questMonitorScale                         	% apply choice
            case BUTTON_CONFIRM                             % check confirmation
                confirmMonitorScale = 1;                  	% confirm parameters
                fprintf('OK.\n'); % parameters confirmed
            case BUTTON_TRY                     % try again
                fprintf('\nTrying again...\n\n'); % starting over
            case BUTTON_CANCEL                  % quit
                fprintf('\nQuitting...');     % quitting
                quitProgram = 1;
                return;                         % exit program
            otherwise                           % cancel
                fprintf('\nQuitting...');     % quitting
                quitProgram = 1;
                return;                         % exit program
        end
    end
    
    fprintf('Checking physical setup...'); % checking setup
    promptQuestCheck_list = {...
        {'Is the counter electrode connected to the PlexStim Analog IO GND?',...
        '(STIM OUT Ch 11 or 25)'},...
        'Is the PlexStim "V MONITOR" connected to CH 1 of the oscilloscope?',...
        'Is the PlexStim "I MONITOR" connected to CH 2 of the oscilloscope?',...
        'Is the oscilloscope turned on?',...
        'Is the PlexStim turned on?',...
        'Is the Stim-2 (Plexon Stimulator - 2.0) software closed?'};
    numOfQuestCheck = length(promptQuestCheck_list);% number of questions
    for n = 1:numOfQuestCheck                       % loop through questions
        promptQuestCheck = promptQuestCheck_list{n};
        questCheck = questdlg(...               % question dialog
            promptQuestCheck,...                % question prompts
            TITLE_QUEST_CHECK,...                 % question litle
            BUTTON_YES,BUTTON_NO,BUTTON_QUIT,...% buttons
            opts);                              % dialog options
        % Confirmation
        switch questCheck                           % apply choice
            case BUTTON_YES                         % check
                if n == numOfQuestCheck             % completed checks
                    confirmPhysSetup = 1;               % setup confirmed
                    fprintf('OK.\n');% continue
                end
            case BUTTON_NO                                      % bad answer
                promptSingleChannel = 'SETUP INCOMPLETE';                    % setup incomplete
                prompt2 = 'Answer MUST be "Yes" to continue!';  % solution
                waitfor(msgbox({promptSingleChannel,prompt2},TITLE_ERROR));   % message
                fprintf('\nQuitting...');                     % quitting
                quitProgram = 1;
                return;                                         % exit program
            case BUTTON_QUIT                % quit
                fprintf('\nQuitting...'); % quitting
                quitProgram = 1;
                return;                     % exit program
            otherwise                       % cancel
                fprintf('\nQuitting...'); % quitting
                quitProgram = 1;
                return;                     % exit program
        end
    end
end

end