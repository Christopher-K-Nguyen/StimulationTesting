function [subjectSelect,serial,stimParam,quitProgram] = getSubjectSelect()
%% Constants
% Buttons
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
confirmSubjectSelect = false;
quitProgram = false;

% Subject list

% allAnimals = 1:NUM_OF_ANIMALS;
% animalList = strings(1,NUM_OF_ANIMALS);
% for animalNum = allAnimals
%     animalList(animalNum) = sprintf('A%02d',animalNum);
% end
animalList = ['A01';'A02';'A03';'A04';'A05';'A06';'A07';'A08';'A09';'A10';'A11';'A12'];
% allControls = 1:NUM_OF_CONTROLS;
% controlList = strings(1,NUM_OF_CONTROLS);
% for controlNum = allControls
%     controlList(controlNum) = sprintf('C%02d',controlNum);
% end
controlList = ['C01';'C02';'C03';'C04';'C05';'C06';'C07';'C08'];
subjectList = [animalList;controlList];

% Stimulation configuration
phaseWidth1 = 200;     % first phase pulse width
interphaseDelay = 100; % interphase delay
phaseWidth2 = 200;     % second phase pulse width
stimRate = 200;        % stimulation rate
numOfPulses = Inf;     % number of pulses

stimParam = struct(...	% initialize structure
    'Amplitude1',[],...       % first phase amplitude
    'PhaseWidth1',phaseWidth1,...    % first phase pulse width
    'InterphaseDelay',interphaseDelay,...     	% interphase delay
    'Amplitude2',[],...       % voltage transient
    'PhaseWidth2',phaseWidth2,...    % second phase pulse width
    'StimulationRate',stimRate,...     % stimulation rate
    'NumberOfPulses',numOfPulses);         % number of pulses

%% Function
while confirmSubjectSelect == false
    fprintf('Enter subject selection...');
    promptQuestListCh_cell = cellstr(subjectList);
    
    % Dialog box
    titleListSubject = 'Subject Selection';      % list title
    promptListCh = {...                     % list prompts
        'Select subject configuration.'};  % insrtuction
    [subjectSelect_idx,listSubject_tf] = listdlg(...	% list dialog
        'PromptString',promptListCh,... % list prompts
        'ListString',promptQuestListCh_cell,...    % list
        'Name',titleListSubject,...
        'InitialValue',[],...
        'SelectionMode','single');           
    
    % Collect input
    if listSubject_tf == false             % cancel detected
        fprintf('\nQuitting...'); % quitting
        quitProgram = true;
        return;                     % exit program
    end
    subjectSelect_string = subjectList(subjectSelect_idx,:);
    subjectSelect = char(subjectSelect_string);
    
    % Confirm subject selection
    titleQuestSubjectSelect = 'Confirm Subject Selection';
    % Format questions
    promptQuestSubjectSelect = sprintf('Subject selected: {\\bf%s}',subjectSelect);
    % Question box
    questListSubject = questdlg(...                      % question dialog
        promptQuestSubjectSelect,...                       % question prompts
        titleQuestSubjectSelect,...                        % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questListSubject                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmSubjectSelect = true;               % confirm info
            fprintf('OK.\n');  % info confirmed
        case BUTTON_TRY                             % try again
            confirmSubjectSelect = false;               % trying again
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

% Amplitude
switch subjectSelect
    case {'A01';'A02';'A03';'C01';'C02'}
        amplitude1 = [0 0 -20 -20 -50 -50 -100 -100]; % first phase amplitude
    case {'A04';'A05';'A06';'C03';'C04'}
        amplitude1 = [-100 -100 0 0 -20 -20 -50 -50]; % first phase amplitude
    case {'A07';'A08';'A09';'C05';'C06'}
        amplitude1 = [-50 -50 -100 -100 0 0 -20 -20]; % first phase amplitude
    case {'A10';'A11';'A12';'C07';'C08'}
        amplitude1 = [-20 -20 -50 -50 -100 -100 0 0]; % first phase amplitude
end
amplitude2 = -amplitude1; % second phase amplitude
stimParam.Amplitude1 = [amplitude1 amplitude1];
stimParam.Amplitude2 = [amplitude2 amplitude2];

% Serial Number
num_char = subjectSelect(2:end);
num = str2double(num_char) + 76;
serial = sprintf('7603-%06d',num);
end