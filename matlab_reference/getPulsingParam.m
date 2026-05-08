function [File,quitProgram] = getPulsingParam(File)
%% Constants
% Buttons
BUTTON_YES = 'YES';
BUTTON_NO = 'NO';
BUTTON_SHIRT = 'Short-term';
BUTTON_LONG = 'Long-term';
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX
% Values
% NUM_OF_ANIMALS = 12;
% NUM_OF_CONTROLS = 8;
% NUM_OF_SUBJECTS = NUM_OF_ANIMALS + NUM_OF_CONTROLS;

%% Variables
isPulsing = false;
confirmPulsing = false;
quitProgram = false;

%% Pulsing
while ~confirmPulsing
    fprintf('Select pulsing...');
    % Question box
    questPulsing = questdlg(...
        'Do you want to do pulsing?',...
        'Pulsing',...
        BUTTON_YES,BUTTON_NO,...
        opts);
    switch questPulsing
        case BUTTON_YES
            isPulsing = true;
        case BUTTON_NO
            isPulsing = false;
    end
    choice = sprintf('Perform Pulsing: {\\bf%s}',questPulsing);
    questChoice = questdlg(...
        choice,...
        'Pulsing',...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
        opts);

    % Confirmation
    switch questChoice                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmPulsing = true;               % confirm info
            fprintf('%s\n',questPulsing);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmPulsing = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            return;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            return;                                 % exit program
    end
end

if isPulsing
    confirmPulsingType = false;
    while ~confirmPulsingType
        fprintf('Select pulsing type...');
        % Question box
        questPulsingType = questdlg(...
            'Select pulsing type.',...
            'Pulsing Type',...
            BUTTON_SHIRT,BUTTON_LONG,...
            opts);
        switch questPulsingType
            case BUTTON_SHIRT
                type = 'SHORT';
            case BUTTON_LONG
                type = 'LONG';
        end

        % Confirmation
        choice = sprintf('Pulsing Type: {\\bf%s}',questPulsingType);
        questChoice = questdlg(...
            choice,...
            'Pulsing Type',...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
            opts);
        switch questChoice                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmPulsingType = true;               % confirm info
                fprintf('%s\n',questPulsingType);  % info confirmed
            case BUTTON_TRY                             % try again
                confirmPulsingType = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                return;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                return;                                 % exit program
        end
    end

    confirmStimRate = false;
    while ~confirmStimRate
        fprintf('Enter stimulation rate...');
        % Question box
        inputStimRate = inputdlg(...
            'Stimulation Rate ({\bfpps}): ',...
            'Stimulation Rate',...
            [1 35],...
            {'200'},...
            opts);

        stimRate = str2double(inputStimRate{1});
        choice = sprintf('Stimulation Rate: {\\bf%g pps}',stimRate);
        questChoice = questdlg(...
            choice,...
            'Stimulation Rate',...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
            opts);

        % Confirmation
        switch questChoice                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmStimRate = true;               % confirm info
                fprintf('%g pps\n',stimRate);
                %             fprintf('OK.\n');  % info confirmed
            case BUTTON_TRY                             % try again
                confirmStimRate = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                return;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                return;                                 % exit program
        end
    end

    confirmNumOfPulses = false;
    while ~confirmNumOfPulses
        fprintf('Total Pulses...');
        % Question box
        inputNumOfPulses = inputdlg(...
            'Total Pulses ({\bfUse "e" for scientific notation}): ',...
            'Total Pulses',...
            [1 35],...
            {'7.2e5'},...
            opts);

        totalPulses = str2double(inputNumOfPulses{1});
        totalPulses_use = addCommas(totalPulses);
        choice = sprintf('Total Pulses: {\\bf%s pulses}',totalPulses_use);
        questChoice = questdlg(...
            choice,...
            'Total Pulses',...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
            opts);

        % Confirmation
        switch questChoice                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmNumOfPulses = true;               % confirm info
                fprintf('%g pulses\n',totalPulses);
                %             fprintf('OK.\n');  % info confirmed
            case BUTTON_TRY                             % try again
                confirmNumOfPulses = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                return;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                return;                                 % exit program
        end
    end

    if strcmpi(type,'LONG')
        confirmCycling = false;
        while ~confirmCycling
            fprintf('Pause Cycle...');
            % Question box
            inputPauseCycle = inputdlg(...
                'Number of pulses for pause ({\bfUse "e" for scientific notation}): ',...
                'Pause Cycle',...
                [1 35],...
                {'7.2e5'},...
                opts);

            pauseCycle = str2double(inputPauseCycle{1});
            choice = sprintf('Number of pulses for pause: {\\bf%g pulses}',pauseCycle);
            questChoice = questdlg(...
                choice,...
                'Pause Cycle',...
                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
                opts);

            % Confirmation
            switch questChoice                              % apply choice
                case BUTTON_CONFIRM                         % check confirmation
                    confirmCycling = true;               % confirm info
                    fprintf('%g pps\n',pauseCycle);
                    %             fprintf('OK.\n');  % info confirmed
                case BUTTON_TRY                             % try again
                    confirmCycling = false;               % trying again
                    fprintf('\nTrying agin...\n\n');          % starting over
                case BUTTON_CANCEL                          % quit
                    fprintf('\nQuitting...');             % quitting
                    quitProgram = true;
                    return;                                 % exit program
                otherwise                                   % cancel
                    fprintf('\nQuitting...');             % quitting
                    quitProgram = true;
                    return;                                 % exit program
            end
        end
    else
        pauseCycle = 0;
    end
else
    type = '';
    stimRate = 0;       % stimulation rate
    totalPulses = 0;
    pauseCycle = 0;
    data = [];
end
File.Pulsing.Type = type;
File.Pulsing.StimulationRate = stimRate;
File.Pulsing.TotalPulses = totalPulses;
duration = totalPulses / stimRate;
File.Pulsing.Duration = duration;
if strcmpi(type,'LONG')
    File.Pulsing.PauseCycle = pauseCycle;
    File.Pulsing.PauseCycle = data;
end

end