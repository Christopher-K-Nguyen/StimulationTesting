function [...
    amplitude1,...      % first phase amplitude
    pulseWidth1,...     % first phase pulse width
    interphaseDelay,... % interphase delay
    amplitude2,...      % second phase amplitude
    pulseWidth2,...     % second phase pulse width
    stimRate,...        % stimulation rate
    numOfPulses,...     % number of pulses
    amplitude1_sign,... % sign of first phase
    amplitude1_mag,...  % magnitude of first amplitude
    quitProgram]...
    = getStimParam()
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
TITLE_ERROR = 'ERROR';
DIMS_DIALOG = [1 70];       % dialog dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmStimParam = 0;
quitProgram = 0;

%% Function
while confirmStimParam == 0
    chargeBalance = 0;
    while chargeBalance == 0
        disp('Enter stimulation parameters...');
        % Dialog box
        titleInputStimParam = 'Stimulation Parameters'; % input title
        promptInputStimParam = {...                   	% input prompts
            'First phase current amplitude (\muA) {\bf(max \pm1000)}:',...  % first phase amplitude (uA)
            'First phase pulse width (\mus) {\bf(min 5)}:',...              % first phase pulse (us)
            'Interphase delay (\mus):',...                                  % interphase delay (us)
            'Second phase current amplitude (\muA) {\bf(max \pm1000)}:',... % second phase amplitude (uA)
            'Second phase pulse width (\mus) {\bf(min 5)}:',...             % second phase width (us)
            'Stimulation rate (Hz) {\bf(min 0.008, max 50000)}:',...        % stimulation rate (Hz)
            'Number of pulses {\bf(max 32767, 0 for \infty)}:'};            % repetitions

        defaultInputStimParam = {'-20','200','100','20','200','50','0'};    % input defaults
        inputStimParam = inputdlg(...	% input dialog
            promptInputStimParam,...    % input prompts
            titleInputStimParam,...     % input title
            DIMS_DIALOG,...             % dialog dimensions
            defaultInputStimParam,...   % input defaults
            opts);                      % dialog options
        cancelStimParam = isempty(inputStimParam);  % cancel dialog

        % Collect input
        amplitude1_cell = inputStimParam{1};        % first phase amplitude (cell)
        amplitude1 = str2double(amplitude1_cell);   % first phase amplitude (double)
        pulseWidth1_cell = inputStimParam{2};       % first phase width (cell)
        pulseWidth1 = str2double(pulseWidth1_cell); % first phase width (double)
        interphaseDelay_cell = inputStimParam{3};           % interphase delay (cell)
        interphaseDelay = str2double(interphaseDelay_cell); % interphase delay (double)
        amplitude2_cell = inputStimParam{4};        % second phase amplitude (cell)
        amplitude2 = str2double(amplitude2_cell);	% second phase amplitude (double)
        pulseWidth2_cell = inputStimParam{5};       % second phase width (cell)
        pulseWidth2 = str2double(pulseWidth2_cell); % second phase width (double)
        stimRate_cell = inputStimParam{6};      % stimulation rate (cell)
        stimRate = str2double(stimRate_cell);   % stimulation rate (double)
        numOfPulses_cell = inputStimParam{7};       % number of pulses (cell)
        numOfPulses = str2double(numOfPulses_cell); % number of pulses (double)    
        if cancelStimParam == 1                     % cancel detected
            fprintf('Quitting...\n\n');             % quitting
            quitProgram = 1;
            return;                                 % exit program
        end
        
        % Sign of first phase
        amplitude1_sign = sign(amplitude1);	% sign of first phase amplitude
        amplitude1_mag = abs(amplitude1);    % magnitude of first phase amplitude
        
        phase1 = abs(amplitude1) * pulseWidth1;
        phase2 = abs(amplitude2) * pulseWidth2;
        opposite = sign(amplitude1) * sign(amplitude2);
        if (phase1 == phase2) && (opposite == -1)
            chargeBalance = 1;
        else
            prompt1 = 'WAVEFORM IS NOT CHARGE-BALANCED!';
            disp(prompt1);
            prompt2 = 'Please enter correct parameters.';
            prompt = {prompt1,prompt2};
            balance = questdlg(prompt,TITLE_ERROR,BUTTON_TRY,BUTTON_CANCEL,opts);
            switch balance
                case BUTTON_TRY                     % try again
                    fprintf('Trying again...\n\n'); % starting over
                case BUTTON_CANCEL                  % quit
                    fprintf('Quitting...\n\n');     % quitting
                    quitProgram = 1;
                    return;                         % exit program
                otherwise                           % cancel
                    fprintf('Quitting...\n\n');     % quitting
                    quitProgram = 1;
                    return;                         % exit program
            end
        end
    end

    % Confirm stimulation parameters
    titleQuestStimParam = 'Confirm Stimulation Parameters';
    % Format questions
    amplitude1_use = sprintf('First phase amplitude: {\\bf%d \\muA}',amplitude1);       % formatted first phase amplitude
    pulseWidth1_use = sprintf('First phase pulse width: {\\bf%d \\mus}',pulseWidth1);   % formatted first phase width
    interphaseDelay_use = sprintf('Interphase delay: {\\bf%d \\mus}',interphaseDelay);  % formatted interphase delay
    amplitude2_use = sprintf('Second phase amplitude: {\\bf%d \\muA}',amplitude2);      % formatted second phase amplitude
    pulseWidth2_use = sprintf('Second phase pulse width: {\\bf%d \\mus}',pulseWidth2);  % formatted second phase width
    stimRate_use = sprintf('Stimulation rate: {\\bf%d Hz}',stimRate);               	% formatted stimulation rate
    if numOfPulses == 0                                                             % formatted repetitions
        numOfPulses_use = sprintf('Number of pulses: {\\bf\\infty pulses}');        % formatted infinite repetitions
    else
        numOfPulses_use = sprintf('Number of pulses: {\\bf%d pulses}',numOfPulses); % formatted finite repetitions
    end
    promptQuestStimParam = {...	% dialog questions
        amplitude1_use,...      % inputted first phase amplitude
        pulseWidth1_use,...     % inputted first phase width
        interphaseDelay_use,... % inputted interphase delay
        amplitude2_use,...      % inputted second phase amplitude
        pulseWidth2_use,...     % inputted second phase width
        stimRate_use,...        % inputted stimulation rate
        numOfPulses_use};       % inputted repetitions

    % Question box
    questStimParam = questdlg(...                   % question dialog
        promptQuestStimParam,...                    % question prompts
        titleQuestStimParam,...                   	% question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questStimParam                               % apply choice
        case BUTTON_CONFIRM                             % check confirmation
            confirmStimParam = 1;                       % confirm parameters
            fprintf('Stimulation parameters set.\n\n'); % parameters confirmed
        case BUTTON_TRY                     % try again
            fprintf('Trying again...\n\n'); % starting over
        case BUTTON_CANCEL                  % quit
            fprintf('Quitting...\n\n');     % quitting
            quitProgram = 1;
            return;                         % exit program
        otherwise                           % cancel
            fprintf('Quitting...\n\n');     % quitting
            quitProgram = 1;
            return;                         % exit program
    end
end
    
end